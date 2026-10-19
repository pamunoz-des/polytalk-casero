# SPDX-FileCopyrightText: 2026 BizzAppDev Systems Pvt. Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Translation pipeline service.

Orchestrates the full translation pipeline: transcription -> translation -> TTS.

Optimizations:
- Connection pre-warming: Pre-establish connections to reduce latency
- Parallel processing: Translate previous chunk while ASR processes next chunk
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Optional, AsyncGenerator

from .base import TranslationResult, TTSResult
from .whisper_service import WhisperService
from .translation_service import TranslationService
from .tts_service import TTSService
from ..config import get_config
from ..utils.config import get_custom_instruction_max_chars, parse_bool_config
from ..utils.logger import get_logger
from ..utils.sanitize import normalize_instruction

logger = get_logger(__name__)


@dataclass
class TranslatedSentence:
    """Track translated sentence with sequence number for TTS ordering."""

    text: str
    sequence: int
    queued_at: float = 0.0


@dataclass
class TranslationContextWindow:
    """Bounded per-stream source/target context for translation prompts."""

    enabled: bool
    max_chunks: int
    max_chars: int
    items: list[dict[str, str]] = field(default_factory=list)

    def snapshot(self) -> Optional[list[dict[str, str]]]:
        if not self.enabled or not self.items:
            return None
        return [dict(item) for item in self.items]

    def remember(self, source_text: str, translated_text: str) -> None:
        if not self.enabled or self.max_chunks <= 0 or self.max_chars <= 0:
            return

        source_text = " ".join(source_text.strip().split())
        translated_text = " ".join(translated_text.strip().split())
        if not source_text or not translated_text:
            return

        self.items.append({"source": source_text, "target": translated_text})
        while len(self.items) > self.max_chunks:
            self.items.pop(0)

        while self._char_count() > self.max_chars and len(self.items) > 1:
            self.items.pop(0)

    def clear(self) -> None:
        self.items.clear()

    def _char_count(self) -> int:
        return sum(
            len(item.get("source", "")) + len(item.get("target", ""))
            for item in self.items
        )


class TranslationPipelineService:
    """
    Orchestrates the full speech-to-speech translation pipeline.

    Coordinates transcription, translation, and TTS services.
    Handles errors gracefully and returns partial results when possible.

    Optimizations:
    - Connection pre-warming: Pre-establish connections on initialization
    - Parallel ASR+Translation: Translate chunk N-1 while ASR processes chunk N
    """

    def __init__(
        self,
        whisper_service: Optional[WhisperService] = None,
        translation_service: Optional[TranslationService] = None,
        tts_service: Optional[TTSService] = None,
        warm_connections: bool = True,
        confirmed_transcripts_only: bool = False,
    ) -> None:
        """
        Initialize the translation pipeline.

        Args:
            whisper_service: Optional Whisper service instance
            translation_service: Optional Translation service instance
            tts_service: Optional TTS service instance
            warm_connections: Whether to pre-warm connections (default: True)
        """
        self.confirmed_transcripts_only = confirmed_transcripts_only
        self.whisper = whisper_service or WhisperService()
        self.translation = translation_service or TranslationService()
        self.tts = tts_service or TTSService()
        self.media_dir = get_config().media_output_dir

        logger.info("TranslationPipelineService initialized")

        # Connection pre-warming state
        self._whisper_warmed = False
        self._translation_warmed = False
        self._warm_lock = asyncio.Lock()

        # Warm connections if requested and not in mock mode
        if (
            warm_connections
            and not self.translation.mock_mode
            and not self.whisper.mock_mode
        ):
            logger.info("Pre-warming connections in background...")
            asyncio.create_task(self._warm_connections())

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures cleanup even on exceptions."""
        await self.close()

    async def close(self) -> None:
        """
        Close all services and cleanup resources.

        Should be called when the pipeline is no longer needed to properly
        cleanup connection pools.
        """
        logger.info("Closing TranslationPipelineService...")

        await self.whisper.close()
        await self.translation.close()
        await self.tts.close()

        logger.info("TranslationPipelineService closed")

    async def warm_connections(self) -> None:
        """Warm provider connections that can be safely prepared before audio."""
        await self._warm_connections()

    async def _synthesize(
        self, text: str, language: str, save_media: bool, tts_engine: str = "auto"
    ) -> TTSResult:
        """
        Synthesize speech from text.

        Args:
            text: Text to convert to speech
            language: Language code
            save_media: Whether to save the audio file

        Returns:
            TTSResult
        """
        if save_media:
            import uuid

            unique_id = uuid.uuid4().hex[:8]
            output_path = self.media_dir / f"tts_{language}_{unique_id}.wav"
            output_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            output_path = None

        return await self.tts.synthesize(text, language, output_path, provider_override=tts_engine)

    async def _warm_connections(self) -> None:
        """
        Pre-warm connections to Whisper and Translation services.

        This reduces latency by establishing connections before the first
        audio chunk arrives.
        """
        async with self._warm_lock:
            if self._whisper_warmed and self._translation_warmed:
                return

            logger.info("Starting connection pre-warming...")

            try:
                # Warm translation connection with a dummy request
                if not self._translation_warmed and not self.translation.mock_mode:
                    logger.info("Warming translation connection...")
                    try:
                        # Send a tiny dummy translation to establish connection
                        await self.translation.translate("test", "en", "en")
                        self._translation_warmed = True
                        logger.info("Translation connection warmed")
                    except Exception as e:
                        logger.warning(
                            f"Translation warm-up failed (will retry on demand): {e}"
                        )

                # Whisper WebSocket will be warmed on first stream_transcribe call
                # We can't pre-warm WebSocket without audio input

                logger.info("Connection pre-warming complete")
            except Exception as e:
                logger.error(f"Connection pre-warming error: {e}")

    @staticmethod
    def _normalize_transcript_text(text: str) -> str:
        """Normalize transcript whitespace for stable delta detection."""
        return " ".join(text.strip().split())

    @staticmethod
    def _find_suffix_overlap_end(
        previous_words: list[str],
        current_words: list[str],
        min_overlap: int,
    ) -> Optional[int]:
        """Return the end index of the strongest previous-suffix match in current."""
        max_overlap = min(len(previous_words), len(current_words))
        for overlap in range(max_overlap, min_overlap - 1, -1):
            suffix = previous_words[-overlap:]
            for index in range(len(current_words) - overlap, -1, -1):
                if current_words[index : index + overlap] == suffix:
                    return index + overlap
        return None

    @classmethod
    def _extract_new_transcript_text(cls, current_text: str, previous_text: str) -> str:
        """
        Extract only newly appended transcript text from cumulative ASR output.

        The STT stream emits cumulative transcripts and may also rewrite earlier
        words. Ambiguous rewrites are treated as corrections, not new speech, to
        avoid retranslating and re-speaking the full transcript.
        """
        current = cls._normalize_transcript_text(current_text)
        previous = cls._normalize_transcript_text(previous_text)

        if not current or current == previous:
            return ""
        if not previous:
            return current

        if current.startswith(previous):
            return current[len(previous) :].strip()

        previous_words = previous.split()
        current_words = current.split()
        min_overlap = min(2, len(previous_words), len(current_words))

        if min_overlap:
            overlap_end = cls._find_suffix_overlap_end(
                previous_words,
                current_words,
                min_overlap,
            )
            if overlap_end is not None and overlap_end < len(current_words):
                return " ".join(current_words[overlap_end:]).strip()

        return ""

    @staticmethod
    def _normalize_language_code(language: Optional[str]) -> str:
        """Normalize detected/provider language codes for pair matching."""
        return (language or "").replace("-", "_").split("_")[0].lower()

    @classmethod
    def _language_match_codes(cls, language: Optional[str]) -> set[str]:
        """Return language codes considered equivalent for conversation matching."""
        base = cls._normalize_language_code(language)
        if not base:
            return set()
        if base in {"hi", "ur"}:
            return {"hi", "ur"}
        return {base}

    @classmethod
    def _conversation_direction(
        cls,
        detected_language: Optional[str],
        source_language: str,
        target_language: str,
    ) -> tuple[str, str]:
        """Return translation direction for a bidirectional conversation turn."""
        detected_matches = cls._language_match_codes(detected_language)
        source_matches = cls._language_match_codes(source_language)
        target_matches = cls._language_match_codes(target_language)
        if (
            detected_matches
            and detected_matches & target_matches
            and not detected_matches & source_matches
        ):
            return target_language, source_language
        if detected_matches and not detected_matches & source_matches:
            logger.info(
                "Conversation STT language did not match selected pair; using default direction "
                f"detected={detected_language} source={source_language} target={target_language}"
            )
        return source_language, target_language

    async def _process_conversation_streaming(
        self,
        audio_generator: AsyncGenerator[bytes, None],
        source_language: str,
        target_language: str,
        save_media: bool = True,
        pause_event: Optional[asyncio.Event] = None,
        visual_context_queue: Optional[asyncio.Queue] = None,
        custom_instruction: Optional[str] = None,
        custom_instruction_queue: Optional[asyncio.Queue] = None,
        tts_engine: str = "auto",
    ) -> AsyncGenerator[dict, None]:
        """Process pause-delimited bidirectional conversation turns."""
        logger.info(
            f"Starting conversation pipeline: {source_language} <-> {target_language}"
        )
        pause_event = pause_event or asyncio.Event()
        full_transcript = ""
        turn_id = 0
        visual_context_summary = None
        custom_instruction_max_chars = get_custom_instruction_max_chars()
        current_custom_instruction = normalize_instruction(
            custom_instruction,
            custom_instruction_max_chars,
        )
        translation_context = TranslationContextWindow(
            enabled=False, max_chunks=0, max_chars=0
        )

        async def paused_audio_generator():
            async for chunk in audio_generator:
                while pause_event.is_set():
                    await asyncio.sleep(0.1)
                yield chunk

        async def drain_visual_context_updates() -> None:
            nonlocal visual_context_summary
            if visual_context_queue is None:
                return
            while True:
                try:
                    summary = visual_context_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                visual_context_summary = " ".join(str(summary or "").split()) or None

        async def drain_custom_instruction_updates() -> None:
            nonlocal current_custom_instruction
            if custom_instruction_queue is None:
                return
            updated = False
            while True:
                try:
                    instruction = custom_instruction_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                current_custom_instruction = normalize_instruction(
                    instruction,
                    custom_instruction_max_chars,
                )
                updated = True
            if updated:
                logger.info(
                    "Custom translation instruction updated: "
                    f"chars={len(current_custom_instruction or '')}"
                )

        try:
            async for trans_result in self.whisper.stream_transcribe(
                paused_audio_generator(),
                None,
                emit_policy="pause",
                candidate_languages=[source_language, target_language],
            ):
                await drain_visual_context_updates()
                await drain_custom_instruction_updates()
                if not trans_result.success:
                    yield {"type": "error", "error": trans_result.error}
                    continue

                current_text = trans_result.text.strip()
                text_to_translate = self._extract_new_transcript_text(
                    current_text, full_transcript
                )
                full_transcript = current_text or full_transcript
                if len(text_to_translate.strip()) < 2:
                    continue

                turn_source, turn_target = self._conversation_direction(
                    trans_result.language, source_language, target_language
                )
                turn_id += 1
                yield {
                    "type": "transcription",
                    "transcript": current_text,
                    "detected_language": trans_result.language,
                    "source_language": turn_source,
                    "target_language": turn_target,
                    "is_partial": trans_result.is_partial,
                    "success": True,
                }

                result = await self.translation.translate(
                    text_to_translate,
                    turn_source,
                    turn_target,
                    context=translation_context.snapshot(),
                    visual_context=visual_context_summary,
                    custom_instruction=current_custom_instruction,
                )
                if not result.success:
                    yield {
                        "type": "error",
                        "error": result.error or "Translation failed",
                    }
                    continue

                yield {
                    "type": "conversation_turn",
                    "turn_id": turn_id,
                    "source_language": turn_source,
                    "target_language": turn_target,
                    "detected_language": trans_result.language,
                    "transcript": text_to_translate,
                    "translated_text": result.text,
                }

                tts_result = await self._synthesize(
                    result.text, turn_target, save_media=False, tts_engine=tts_engine
                )
                if tts_result.success:
                    yield {
                        "type": "tts",
                        "audio_url": tts_result.audio_url,
                        "sequence": turn_id,
                        "turn_id": turn_id,
                        "language": turn_target,
                    }
                elif tts_result.error:
                    yield {"type": "error", "error": tts_result.error}

            yield {"type": "complete"}
        except (GeneratorExit, asyncio.CancelledError):
            logger.info("Conversation pipeline cancelled")
            raise
        except Exception as e:
            logger.error(f"Conversation pipeline error: {e}")
            yield {"type": "error", "error": str(e), "success": False}

    async def process_streaming(
        self,
        audio_generator: AsyncGenerator[bytes, None],
        source_language: str,
        target_language: str,
        save_media: bool = True,
        pause_event: Optional[asyncio.Event] = None,
        language_swap_queue: Optional[asyncio.Queue] = None,
        visual_context_queue: Optional[asyncio.Queue] = None,
        mode: str = "live",
        custom_instruction: Optional[str] = None,
        custom_instruction_queue: Optional[asyncio.Queue] = None,
        tts_engine: str = "auto",
    ) -> AsyncGenerator[dict, None]:
        """
        Process streaming audio with the real-time translation pipeline.

        Architecture:
        - ASR worker: Continuously streams audio to Whisper, yields transcriptions
        - Translation worker: Receives transcriptions, translates, generates TTS

        Benefits:
        - True parallelism: ASR processes N while translating N-1
        - Better latency: No waiting for full accumulation
        - Clean separation: Each thread has single responsibility

        Args:
            audio_generator: Async generator yielding audio chunks
            source_language: Source language code
            target_language: Target language code
            save_media: Whether to save generated media files
            pause_event: Optional asyncio.Event to signal pause (set=paused, clear=resume)
            language_swap_queue: Optional asyncio.Queue for receiving language swap updates
            visual_context_queue: Optional asyncio.Queue for shared tab/page visual
                context summary updates
            custom_instruction: Optional user-provided translation guidance
            custom_instruction_queue: Optional asyncio.Queue for runtime custom
                translation instruction updates

        Yields:
            Dictionary with streaming pipeline results
        """

        if mode == "conversation":
            async for result in self._process_conversation_streaming(
                audio_generator,
                source_language,
                target_language,
                save_media=save_media,
                pause_event=pause_event,
                visual_context_queue=visual_context_queue,
                custom_instruction=custom_instruction,
                custom_instruction_queue=custom_instruction_queue,
                tts_engine=tts_engine,
            ):
                yield result
            return

        logger.info(
            f"Starting streaming pipeline: {source_language} -> {target_language}"
        )

        # Get language swap delay from config (default 200ms)
        from ..config import get_config

        config = get_config()
        app_config = config.app
        translation_config = config.translation

        def int_config(key: str, default: int) -> int:
            try:
                return int(app_config.get(key, default))
            except (TypeError, ValueError):
                return default

        def translation_int_config(key: str, default: int) -> int:
            try:
                return int(translation_config.get(key, default))
            except (TypeError, ValueError):
                return default

        def float_config(key: str, default: float) -> float:
            try:
                return float(app_config.get(key, default))
            except (TypeError, ValueError):
                return default

        language_swap_delay_ms = int_config("language_swap_delay_ms", 200)
        language_swap_delay_sec = language_swap_delay_ms / 1000.0
        translation_flush_chars = int_config("translation_flush_chars", 120)
        translation_flush_seconds = float_config("translation_flush_seconds", 2.0)
        translation_flush_min_chars = int_config("translation_flush_min_chars", 40)
        translation_context_enabled = parse_bool_config(
            translation_config.get("context_enabled"), True
        )
        translation_context_max_chunks = max(
            0, translation_int_config("context_max_chunks", 4)
        )
        translation_context_max_chars = max(
            0, translation_int_config("context_max_chars", 1200)
        )

        # Create queues (maxsize=100 for safety, but effectively unlimited)
        trans_queue = asyncio.Queue(maxsize=100)  # ASR → Translation
        tts_queue = asyncio.Queue(
            maxsize=100
        )  # Translation → TTS (increased to prevent backpressure)
        result_queue = asyncio.Queue(maxsize=100)
        stop_event = asyncio.Event()
        pause_event = pause_event or asyncio.Event()
        language_swap_queue = language_swap_queue or asyncio.Queue()

        # Shared state
        detected_language = source_language
        translation_source_lang = source_language
        target_lang = target_language  # Mutable reference for target language
        swap_state = {"pending": None}  # Use dict to avoid scope issues
        swap_lock = asyncio.Lock()  # Protect swap_state from race conditions

        pipeline_start_time = time.time()
        last_asr_result_time = pipeline_start_time

        def metric_value(metrics: Optional[dict], key: str, default: float = 0.0):
            if not metrics:
                return default
            try:
                return float(metrics.get(key, default))
            except (TypeError, ValueError):
                return default

        # Worker tasks
        asr_task = None
        translation_task = None

        async def paused_audio_generator():
            """Wrapper generator that pauses when pause_event is set."""
            async for chunk in audio_generator:
                while pause_event.is_set():
                    await asyncio.sleep(0.1)
                yield chunk

        async def tts_worker():
            """TTS worker task - generates speech from translated sentences."""
            tts_seq = 0
            pending_items = 0

            try:
                while True:
                    # Wait for item from queue (blocks until available)
                    try:
                        item = await asyncio.wait_for(tts_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        # Timeout - check if we should exit
                        if (
                            stop_event.is_set()
                            and tts_queue.empty()
                            and pending_items == 0
                        ):
                            logger.info("TTS worker: timeout and stop set, exiting")
                            break
                        continue

                    # Poison pill - shutdown signal from translation worker
                    if item is None:
                        logger.info(
                            f"TTS worker: received poison pill, pending={pending_items}, shutting down gracefully"
                        )
                        break

                    pending_items += 1

                    # Process the item

                    # Extract translated sentence
                    translated_sentence = item
                    tts_queue_wait = (
                        time.time() - translated_sentence.queued_at
                        if translated_sentence.queued_at
                        else 0.0
                    )

                    elapsed = time.time() - pipeline_start_time
                    logger.debug(
                        f"[TIMING] TTS sentence {tts_seq} at {elapsed:.2f}s: "
                        f"'{translated_sentence.text[:50]}...'"
                    )

                    # Generate TTS
                    try:
                        tts_result = await self._synthesize(
                            translated_sentence.text,
                            target_lang,
                            save_media=False,
                            tts_engine=tts_engine,
                        )

                        if tts_result.success:
                            await result_queue.put(
                                {
                                    "type": "tts",
                                    "audio_url": tts_result.audio_url,
                                    "sequence": tts_seq,
                                }
                            )
                            duration = getattr(tts_result, "duration", 0.0) or 0.0
                            logger.debug(
                                "[PIPELINE_METRIC] TTS completed "
                                f"seq={tts_seq} queue_wait={tts_queue_wait:.3f}s "
                                f"duration={duration:.3f}s "
                                f"tts_queue={tts_queue.qsize()} "
                                f"result_queue={result_queue.qsize()}"
                            )
                        else:
                            logger.error(
                                f"TTS worker: TTS generation failed - {tts_result.error}"
                            )
                            await result_queue.put(
                                {
                                    "type": "error",
                                    "error": f"TTS generation failed: {tts_result.error}",
                                }
                            )

                    except Exception as e:
                        logger.error(f"TTS worker: error generating TTS: {e}")
                        await result_queue.put({"type": "error", "error": str(e)})
                        pending_items -= 1

                    tts_seq += 1
                    pending_items -= 1

            except asyncio.CancelledError:
                logger.info(f"TTS worker: cancelled, pending={pending_items}")
            except Exception as e:
                logger.error(f"TTS worker error: {e}")
                await result_queue.put({"type": "error", "error": str(e)})

        async def asr_worker():
            """ASR worker task - streams audio to Whisper."""
            nonlocal detected_language, last_asr_result_time
            try:
                logger.info("ASR worker: starting stream_transcribe")
                async for trans_result in self.whisper.stream_transcribe(
                    paused_audio_generator(), detected_language
                ):
                    logger.debug(
                        f"ASR worker: got transcription result, success={trans_result.success}"
                    )
                    if stop_event.is_set():
                        logger.info("ASR worker: stop event set, exiting")
                        break

                    if trans_result.success:
                        if trans_result.language:
                            detected_language = trans_result.language

                        elapsed = time.time() - pipeline_start_time
                        delta = time.time() - last_asr_result_time
                        last_asr_result_time = time.time()
                        logger.debug(
                            f"[TIMING] ASR transcription at {elapsed:.2f}s "
                            f"(+{delta:.2f}s since previous ASR result)"
                        )
                        metrics = trans_result.metrics or {}
                        logger.debug(
                            "[PIPELINE_METRIC] ASR result "
                            f"seq={metrics.get('sequence', 'n/a')} "
                            f"stt_queue_wait={metric_value(metrics, 'queue_wait_seconds'):.3f}s "
                            f"stt_infer={metric_value(metrics, 'inference_seconds'):.3f}s "
                            f"stt_emit_delay={metric_value(metrics, 'emit_delay_seconds'):.3f}s "
                            f"stt_audio={metric_value(metrics, 'audio_duration_seconds'):.3f}s "
                            f"stt_depth={metrics.get('queue_depth_at_enqueue', 'n/a')} "
                            f"asr_to_translation_queue={trans_queue.qsize()}"
                        )

                        queued_at = time.time()
                        await trans_queue.put(
                            {
                                "type": "transcription",
                                "result": trans_result,
                                "queued_at": queued_at,
                            }
                        )
                        logger.debug(
                            "[PIPELINE_QUEUE] ASR->translation enqueued "
                            f"queue_depth={trans_queue.qsize()}"
                        )
                    else:
                        logger.error(
                            f"ASR worker: transcription failed - {trans_result.error}"
                        )
                        await trans_queue.put(
                            {"type": "error", "error": trans_result.error}
                        )

                logger.info("ASR worker: audio generator ended")
            except asyncio.CancelledError:
                logger.info("ASR worker: cancelled")
            except Exception as e:
                logger.error(f"ASR worker error: {e}")
                await trans_queue.put({"type": "error", "error": str(e)})
            finally:
                await trans_queue.put({"type": "done"})

        async def translation_worker():
            """Translation worker task - batches transcript deltas before translation."""
            nonlocal detected_language, translation_source_lang, target_lang
            last_transcribed_text = ""
            full_translation = ""
            translation_sequence = 0

            # Local buffer for this streaming session (thread-safe)
            translation_buffer = ""
            translation_buffer_started_at = None
            translation_context = TranslationContextWindow(
                enabled=translation_context_enabled,
                max_chunks=translation_context_max_chunks,
                max_chars=translation_context_max_chars,
            )
            visual_context_summary = None
            custom_instruction_max_chars = get_custom_instruction_max_chars()
            current_custom_instruction = normalize_instruction(
                custom_instruction,
                custom_instruction_max_chars,
            )

            async def drain_visual_context_updates() -> None:
                nonlocal visual_context_summary
                if visual_context_queue is None:
                    return

                updated = False
                while True:
                    try:
                        summary = visual_context_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    visual_context_summary = (
                        " ".join(str(summary or "").split()) or None
                    )
                    updated = True

                if updated:
                    logger.info(
                        "Visual context summary updated: "
                        f"chars={len(visual_context_summary or '')}"
                    )

            async def drain_custom_instruction_updates() -> None:
                nonlocal current_custom_instruction
                if custom_instruction_queue is None:
                    return

                updated = False
                while True:
                    try:
                        instruction = custom_instruction_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    current_custom_instruction = normalize_instruction(
                        instruction,
                        custom_instruction_max_chars,
                    )
                    updated = True

                if updated:
                    logger.info(
                        "Custom translation instruction updated: "
                        f"chars={len(current_custom_instruction or '')}"
                    )

            async def enqueue_tts(text: str, sequence: int) -> None:
                await tts_queue.put(
                    TranslatedSentence(
                        text=text,
                        sequence=sequence,
                        queued_at=time.time(),
                    )
                )
                logger.debug(
                    "[PIPELINE_QUEUE] Translation->TTS enqueued "
                    f"seq={sequence} tts_queue={tts_queue.qsize()}"
                )

            async def flush_translation_buffer(reason: str) -> None:
                nonlocal full_translation, translation_buffer
                nonlocal translation_buffer_started_at, translation_sequence

                await drain_visual_context_updates()
                await drain_custom_instruction_updates()

                remaining_text = translation_buffer.strip()
                if not remaining_text:
                    return

                translation_buffer = ""
                translation_buffer_started_at = None
                logger.info(
                    f"Flushing translation buffer ({reason}): "
                    f"'{remaining_text[:80]}...'"
                )
                try:
                    result = await self.translation.translate(
                        remaining_text,
                        translation_source_lang,
                        target_lang,
                        context=translation_context.snapshot(),
                        visual_context=visual_context_summary,
                        custom_instruction=current_custom_instruction,
                    )
                    if result.success:
                        translation_context.remember(remaining_text, result.text)
                        full_translation += " " + result.text
                        full_translation = full_translation.strip()
                        await result_queue.put(
                            {
                                "type": "translation",
                                "translated_text": full_translation,
                            }
                        )
                        await enqueue_tts(result.text, translation_sequence)
                        translation_sequence += 1
                    else:
                        logger.warning(
                            f"Failed to flush buffer ({reason}): {result.error}"
                        )
                except Exception as e:
                    logger.error(f"Failed to flush buffer ({reason}): {e}")

            try:
                while True:
                    if stop_event.is_set():
                        logger.info("Translation worker: stop event set, exiting")
                        break

                    # Check for language swap updates (non-blocking)
                    try:
                        while True:
                            swap_update = language_swap_queue.get_nowait()
                            if swap_update:
                                async with swap_lock:
                                    swap_state["pending"] = swap_update
                                logger.info(
                                    f"Language swap queued: {swap_update.get('source_language')} -> "
                                    f"{swap_update.get('target_language')}"
                                )
                    except asyncio.QueueEmpty:
                        pass

                    # Wait for transcription with short timeout for responsiveness
                    try:
                        msg = await asyncio.wait_for(trans_queue.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        await drain_visual_context_updates()
                        await drain_custom_instruction_updates()
                        if translation_buffer.strip() and translation_buffer_started_at:
                            buffer_age = time.time() - translation_buffer_started_at
                            if buffer_age >= translation_flush_seconds:
                                await flush_translation_buffer("idle timeout")
                        if stop_event.is_set():
                            break
                        continue

                    if msg["type"] == "done":
                        logger.info(
                            "Translation worker: ASR done, finishing translations"
                        )
                        await flush_translation_buffer("asr done")
                        await tts_queue.put(None)
                        break

                    if msg["type"] == "error":
                        logger.error(
                            f"Translation worker: received error - {msg['error']}"
                        )
                        await result_queue.put(msg)
                        continue

                    await drain_visual_context_updates()
                    await drain_custom_instruction_updates()

                    trans_result = msg["result"]
                    asr_translation_queue_wait = (
                        time.time() - msg["queued_at"] if msg.get("queued_at") else 0.0
                    )
                    logger.debug(
                        "[PIPELINE_METRIC] Translation worker received "
                        f"asr_queue_wait={asr_translation_queue_wait:.3f}s "
                        f"trans_queue={trans_queue.qsize()} "
                        f"tts_queue={tts_queue.qsize()} "
                        f"result_queue={result_queue.qsize()}"
                    )
                    current_text = trans_result.text.strip()
                    trans_metrics = trans_result.metrics or {}
                    is_pause_flush = bool(trans_metrics.get("force_emit"))

                    if not current_text:
                        continue

                    # Check for pending language swap
                    # CRITICAL: Must flush buffer FIRST with current language before swapping
                    if swap_state["pending"] and translation_buffer.strip():
                        # Flush remaining buffer with CURRENT language before applying swap
                        await flush_translation_buffer("language swap")

                    # Now apply the language swap with configurable delay
                    if swap_state["pending"]:
                        # Apply configurable delay for smoother transition
                        await asyncio.sleep(language_swap_delay_sec)
                        async with swap_lock:
                            detected_language = swap_state["pending"]["source_language"]
                            translation_source_lang = swap_state["pending"][
                                "source_language"
                            ]
                            target_lang = swap_state["pending"]["target_language"]
                            swap_state["pending"] = None
                            translation_context.clear()
                        logger.info(
                            f"Language swap applied: {detected_language} -> {target_lang} (delay: {language_swap_delay_ms}ms)"
                        )

                    # Forward transcription to frontend
                    await result_queue.put(
                        {
                            "type": "transcription",
                            "result": trans_result,
                        }
                    )

                    # Log all text updates for debugging
                    logger.debug(
                        f"ASR: len={len(current_text)}, last_len={len(last_transcribed_text)}, "
                        f"text='{current_text[:100]}...'"
                    )

                    # Parakeet: translate only confirmed utterances.
                    if self.confirmed_transcripts_only and not is_pause_flush:
                        continue

                    # STT emits cumulative text, so translate only the new delta.
                    text_to_translate = self._extract_new_transcript_text(
                        current_text,
                        last_transcribed_text,
                    )
                    last_transcribed_text = current_text

                    if not text_to_translate:
                        logger.debug(
                            "Skipping: ASR correction or ambiguous cumulative rewrite"
                        )
                        continue

                    logger.debug(f"New transcript delta: '{text_to_translate[:50]}...'")

                    # Skip if text too short (reduced threshold)
                    if len(text_to_translate.strip()) < 3:
                        logger.debug(
                            f"Skipping: text too short '{text_to_translate[:30]}...' (len={len(text_to_translate)})"
                        )
                        continue

                    elapsed = time.time() - pipeline_start_time
                    logger.debug(
                        f"[TIMING] Translation candidate at {elapsed:.2f}s: "
                        f"'{text_to_translate[:50]}...'"
                    )

                    # Buffer short deltas to avoid low-context translations and TTS chatter.
                    cleaned_text = " ".join(text_to_translate.strip().split())
                    translation_buffer += " " + cleaned_text
                    translation_buffer = translation_buffer.strip()
                    if translation_buffer_started_at is None:
                        translation_buffer_started_at = time.time()

                    is_sentence_complete = bool(
                        re.search(r"[.!?।؟]\s*$", translation_buffer)
                    )
                    buffer_age = time.time() - translation_buffer_started_at

                    # Translate on sentence boundary, size threshold, or short time-based flush.
                    should_translate = (
                        is_sentence_complete
                        or (is_pause_flush and len(translation_buffer) >= 3)
                        or len(translation_buffer) >= translation_flush_chars
                        or (
                            buffer_age >= translation_flush_seconds
                            and len(translation_buffer) >= translation_flush_min_chars
                        )
                    )

                    if not should_translate:
                        logger.debug(
                            f"Accumulating: '{translation_buffer[:50]}...' "
                            f"(complete={is_sentence_complete}, "
                            f"pause_flush={is_pause_flush}, "
                            f"len={len(translation_buffer)})"
                        )
                        continue

                    # Translate the accumulated buffer
                    text_to_send = translation_buffer
                    translation_buffer = ""  # Clear buffer after sending
                    translation_buffer_started_at = None

                    logger.debug(
                        f"Translating accumulated text: '{text_to_send[:80]}...'"
                    )

                    try:
                        # Retry logic for translation API failures
                        max_retries = 3
                        result = None
                        translation_started_at = time.time()
                        attempts_used = 0
                        for attempt in range(max_retries):
                            attempts_used = attempt + 1
                            try:
                                result = await self.translation.translate(
                                    text_to_send,
                                    translation_source_lang,
                                    target_lang,
                                    context=translation_context.snapshot(),
                                    visual_context=visual_context_summary,
                                    custom_instruction=current_custom_instruction,
                                )
                                if result.success:
                                    break
                                logger.warning(
                                    f"Translation attempt {attempt + 1} failed: {result.error}"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"Translation attempt {attempt + 1} error: {e}"
                                )

                            if attempt < max_retries - 1:
                                await asyncio.sleep(
                                    0.5 * (attempt + 1)
                                )  # Exponential backoff

                        translation_duration = time.time() - translation_started_at
                        pipeline_elapsed = time.time() - pipeline_start_time
                        logger.info(
                            "[TIMING] Translation request completed at "
                            f"{pipeline_elapsed:.2f}s in {translation_duration:.2f}s "
                            f"(attempts={attempts_used}, chars={len(text_to_send)})"
                        )

                        if result is None or not result.success:
                            logger.error(
                                f"Translation failed after {max_retries} attempts"
                            )
                            result = TranslationResult(
                                success=False,
                                text="",
                                error="Translation failed after retries",
                            )

                        logger.debug(
                            f"Translation result: success={result.success}, text_len={len(result.text) if result.text else 0}"
                        )

                        if result.success:
                            translation_context.remember(text_to_send, result.text)
                            # Update full translation
                            full_translation += " " + result.text
                            full_translation = full_translation.strip()

                            # Send incremental translation to frontend
                            await result_queue.put(
                                {
                                    "type": "translation",
                                    "translated_text": full_translation,
                                }
                            )

                            # Push to TTS queue immediately for parallel processing
                            await enqueue_tts(result.text, translation_sequence)
                            translation_sequence += 1
                        else:
                            logger.warning(
                                f"Skipping TTS for failed translation: {text_to_send[:50]}..."
                            )
                            # Don't send empty text to TTS - skip to avoid wasting resources
                    except Exception as e:
                        logger.error(f"Translation error: {e}")

                    if trans_result.is_partial is False:
                        await flush_translation_buffer("final transcript")
                        break

                translation_context.clear()
                await result_queue.put({"type": "complete"})

            except asyncio.CancelledError:
                translation_context.clear()
                logger.info("Translation worker: cancelled")
            except Exception as e:
                translation_context.clear()
                logger.error(f"Translation worker error: {e}")
                await result_queue.put({"type": "error", "error": str(e)})

        # Start worker tasks
        asr_task = asyncio.create_task(asr_worker())
        translation_task = asyncio.create_task(translation_worker())
        tts_task = asyncio.create_task(tts_worker())

        try:
            # Consume results from worker tasks
            while True:
                try:
                    result = await asyncio.wait_for(result_queue.get(), timeout=30.0)
                    logger.debug(f"Pipeline result from queue: {result.get('type')}")
                except asyncio.TimeoutError:
                    logger.debug("Result queue timeout, continuing...")
                    continue

                if result.get("type") == "transcription":
                    trans_result = result["result"]
                    logger.debug(f"Yielding transcription: {trans_result.text[:50]}")
                    yield {
                        "type": "transcription",
                        "transcript": trans_result.text,
                        "detected_language": detected_language,
                        "is_partial": trans_result.is_partial,
                        "success": True,
                    }
                elif result.get("type") in ["translation", "tts", "error"]:
                    msg_content = (
                        result.get("translated_text") or result.get("audio_url") or ""
                    )
                    logger.debug(f"Yielding {result.get('type')}: {msg_content[:50]}")
                    yield result
                elif result.get("type") == "complete":
                    logger.info("Streaming pipeline complete")
                    break

        except (GeneratorExit, asyncio.CancelledError):
            logger.info("Client disconnected, cancelling all threads")
            stop_event.set()

            # Cancel ASR first to stop audio input
            asr_task.cancel()
            try:
                await asr_task
            except asyncio.CancelledError:
                pass

            # Wait for translation to finish and send poison pill
            translation_task.cancel()
            try:
                await translation_task
            except asyncio.CancelledError:
                pass

            # Wait for TTS to drain queue (max 5 seconds)
            try:
                await asyncio.wait_for(tts_task, timeout=5.0)
                logger.info("TTS worker drained queue successfully")
            except asyncio.TimeoutError:
                logger.warning("TTS worker timeout, cancelling")
                tts_task.cancel()
                try:
                    await tts_task
                except asyncio.CancelledError:
                    pass

            raise
        except Exception as e:
            logger.error(f"Streaming pipeline error: {e}")
            stop_event.set()

            # Same graceful shutdown pattern
            asr_task.cancel()
            try:
                await asr_task
            except asyncio.CancelledError:
                pass

            translation_task.cancel()
            try:
                await translation_task
            except asyncio.CancelledError:
                pass

            # Wait for TTS to drain queue (max 5 seconds)
            try:
                await asyncio.wait_for(tts_task, timeout=5.0)
                logger.info("TTS worker drained queue successfully")
            except asyncio.TimeoutError:
                logger.warning("TTS worker timeout, cancelling")
                tts_task.cancel()
                try:
                    await tts_task
                except asyncio.CancelledError:
                    pass

            yield {"type": "error", "error": str(e), "success": False}
