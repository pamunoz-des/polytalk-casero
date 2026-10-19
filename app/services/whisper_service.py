# SPDX-FileCopyrightText: 2026 BizzAppDev Systems Pvt. Ltd.
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Whisper transcription service.

Supports both real Whisper API and mock mode for testing.
"""

import asyncio
import io
import json
import wave
from typing import Optional, AsyncGenerator, Callable

import websockets

from .base import BaseTranscriptionService, TranscriptionResult
from ..config import Config, get_config
from ..utils.logger import get_logger

logger = get_logger(__name__)


class WhisperService(BaseTranscriptionService):
    """
    Whisper transcription service using faster-whisper.
    """

    def __init__(self, config: Optional[Config] = None):
        """Initialize Whisper service with configuration."""
        self.config = (config or get_config()).whisper
        self.enabled = self.config.get("enabled", True)
        self.mock_mode = self.config.get("mock_mode", True)
        self.base_url = self.config.get("base_url", "http://stt:8000")
        self.ws_endpoint = self.config.get("ws_endpoint", "/v1/stream/transcriptions")
        self.api_key = self.config.get("api_key", "")
        self.model = self.config.get("model", "whisper-1")
        self.timeout = self.config.get("timeout_seconds", 60)
        self.max_reconnect_attempts = self.config.get("max_reconnect_attempts", 3)
        self.reconnect_delay = self.config.get("reconnect_delay_seconds", 2)
        self.ping_interval = self.config.get("ping_interval_seconds", None)
        self.ping_timeout = self.config.get("ping_timeout_seconds", None)

    async def stream_transcribe(
        self,
        audio_generator: AsyncGenerator[bytes, None],
        language: Optional[str] = None,
        emit_policy: str = "live",
        candidate_languages: Optional[list[str]] = None,
        on_result: Optional[Callable[[TranscriptionResult], None]] = None,
    ) -> AsyncGenerator[TranscriptionResult, None]:
        """
        Stream audio chunks for real-time transcription.

        Args:
            audio_generator: Async generator yielding audio chunks
            language: Optional source language code hint
            emit_policy: STT emission policy, either live or pause
            candidate_languages: Optional language candidates for detection
            on_result: Optional callback for each transcription result

        Yields:
            TranscriptionResult with incremental transcription updates
        """
        if not self.enabled:
            logger.warning("Whisper service is disabled")
            result = TranscriptionResult(
                text="", success=False, error="Whisper service is disabled"
            )
            yield result
            if on_result:
                on_result(result)
            return

        if self.mock_mode:
            logger.info("Using mock streaming transcription")
            async for result in self._mock_stream_transcribe(audio_generator, language):
                yield result
                if on_result:
                    on_result(result)
            return

        try:
            async for result in self._real_stream_transcribe(
                audio_generator,
                language,
                emit_policy=emit_policy,
                candidate_languages=candidate_languages,
            ):
                yield result
                if on_result:
                    on_result(result)
        except Exception as e:
            logger.error(f"Streaming transcription failed: {e}")
            error_result = TranscriptionResult(text="", success=False, error=str(e))
            yield error_result
            if on_result:
                on_result(error_result)

    def _normalize_language_hint(self, language: Optional[str]) -> Optional[str]:
        """
        Normalize UI language codes to ASR language hints.

        Faster Whisper expects base language codes such as "es" or "nl", while
        the UI can use regional codes such as "es_MX" for translation/TTS.
        """
        if not language:
            return None
        return language.replace("-", "_").split("_")[0].lower()

    async def _real_stream_transcribe(
        self,
        audio_generator: AsyncGenerator[bytes, None],
        language: Optional[str] = None,
        emit_policy: str = "live",
        candidate_languages: Optional[list[str]] = None,
    ) -> AsyncGenerator[TranscriptionResult, None]:
        """
        Stream audio chunks using WebSocket for real-time transcription.

        Args:
            audio_generator: Async generator yielding audio chunks
            language: Optional source language code hint
            emit_policy: STT emission policy, either live or pause
            candidate_languages: Optional language candidates for detection

        Yields:
            TranscriptionResult with incremental transcription updates
        """
        # Convert https:// to wss:// and http:// to ws://
        ws_base = self.base_url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )
        ws_url = ws_base.rstrip("/") + self.ws_endpoint

        # Add Authorization header if API key is configured
        additional_headers = {}
        if self.api_key:
            additional_headers["Authorization"] = f"Bearer {self.api_key}"
            logger.info(f"Using API key authentication for WebSocket: {ws_url}")

        reconnect_attempts = 0
        full_text = ""
        language_hint = self._normalize_language_hint(language)

        while reconnect_attempts < self.max_reconnect_attempts:
            ws = None
            try:
                logger.info(f"Connecting to WebSocket: {ws_url}")
                ws = await websockets.connect(
                    ws_url,
                    additional_headers=additional_headers,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                )
                logger.info("WebSocket connection established")

                control_message = {}
                if language_hint:
                    control_message["language"] = language_hint
                if emit_policy:
                    control_message["emit_policy"] = emit_policy
                if candidate_languages:
                    control_message["candidate_languages"] = [
                        self._normalize_language_hint(item)
                        for item in candidate_languages
                        if item
                    ]
                if control_message:
                    await ws.send(json.dumps(control_message))

                # Use asyncio Queue to communicate between tasks
                result_queue = asyncio.Queue()
                send_done = asyncio.Event()
                recv_done = asyncio.Event()
                flush_ack_received = asyncio.Event()
                send_task = None
                recv_task = None

                async def send_chunks():
                    """Send audio chunks to ASR WebSocket."""
                    nonlocal send_done
                    try:
                        async for audio_chunk in audio_generator:
                            # Check for end signal marker
                            if audio_chunk == b"__END_SIGNAL__":
                                if "parakeet" in self.base_url.lower():
                                    await ws.send(json.dumps({"type": "flush"}))
                                    logger.info("Parakeet flush requested before closing")
                                    try:
                                        await asyncio.wait_for(
                                            flush_ack_received.wait(),
                                            timeout=10.0,
                                        )
                                    except asyncio.TimeoutError:
                                        logger.warning(
                                            "Parakeet flush acknowledgement timed out"
                                        )
                                logger.info(
                                    "Received end signal from audio generator, closing WebSocket"
                                )
                                try:
                                    await ws.close()
                                    logger.info("Whisper WebSocket closed")
                                except Exception as e:
                                    logger.debug(f"Error closing WebSocket: {e}")
                                break

                            try:
                                await ws.send(audio_chunk)
                            except Exception as e:
                                logger.error(f"Error sending chunk: {e}")
                                break
                    except asyncio.CancelledError:
                        logger.info("send_chunks task cancelled")
                    except GeneratorExit:
                        logger.info("send_chunks GeneratorExit received")
                    except Exception as e:
                        logger.error(f"Send task error: {e}")
                    finally:
                        send_done.set()

                async def receive_responses():
                    """Receive transcription responses from ASR WebSocket."""
                    nonlocal recv_done
                    try:
                        while True:
                            response = await ws.recv()
                            result_data = json.loads(response)

                            if result_data.get("type") == "flush_ack":
                                flush_ack_received.set()
                                logger.info("Parakeet flush acknowledged")
                                continue

                            if result_data.get("type") == "emit_policy_ack":
                                logger.debug(
                                    "STT emit policy acknowledged: "
                                    f"{result_data.get('emit_policy')}"
                                )
                                continue
                            if result_data.get("type") == "candidate_languages_ack":
                                logger.debug(
                                    "STT candidate languages acknowledged: "
                                    f"{result_data.get('candidate_languages')}"
                                )
                                continue

                            text = result_data.get("text", "")
                            is_final = result_data.get("is_final", False)
                            error = result_data.get("error")
                            metrics = result_data.get("metrics")
                            detected_language = result_data.get("language")

                            if error:
                                await result_queue.put(
                                    {"type": "error", "error": error}
                                )
                                continue

                            # Send the text as-is (incremental update from ASR)
                            if text:
                                await result_queue.put(
                                    {
                                        "type": "result",
                                        "text": text,
                                        "is_partial": not is_final,
                                        "language": detected_language,
                                        "metrics": metrics,
                                    }
                                )
                    except asyncio.CancelledError:
                        logger.info("receive_responses task cancelled")
                    except websockets.exceptions.ConnectionClosed:
                        logger.info("ASR WebSocket closed")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse response: {e}")
                    except Exception as e:
                        logger.error(f"Error receiving response: {e}")
                    finally:
                        recv_done.set()

                # Run both tasks concurrently
                send_task = asyncio.create_task(send_chunks())
                recv_task = asyncio.create_task(receive_responses())

                # Collect results from queue
                try:
                    while not (send_done.is_set() and recv_done.is_set() and result_queue.empty()):
                        try:
                            item = await asyncio.wait_for(
                                result_queue.get(), timeout=1.0
                            )
                            if item is None:
                                continue

                            if item["type"] == "error":
                                yield TranscriptionResult(
                                    text="", success=False, error=item["error"]
                                )
                            elif item["type"] == "result":
                                yield TranscriptionResult(
                                    text=item["text"],
                                    language=item.get("language") or language,
                                    success=True,
                                    is_partial=item["is_partial"],
                                    metrics=item.get("metrics"),
                                )
                        except asyncio.TimeoutError:
                            continue

                    logger.info("Audio streaming complete")
                except GeneratorExit:
                    logger.info("GeneratorExit received in stream_transcribe")
                    raise
                finally:
                    # Cleanup: cancel tasks and close WebSocket
                    logger.info("Cleaning up Whisper WebSocket...")

                    if send_task and not send_task.done():
                        send_task.cancel()
                        try:
                            await send_task
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.debug(f"Send task error on cancel: {e}")
                    if recv_task and not recv_task.done():
                        recv_task.cancel()
                        try:
                            await recv_task
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            logger.debug(f"Recv task error on cancel: {e}")
                    if ws:
                        try:
                            await ws.close()
                        except Exception as e:
                            logger.debug(f"Error closing WebSocket: {e}")
                    return

            except websockets.exceptions.ConnectionClosed as e:
                reconnect_attempts += 1
                logger.warning(
                    f"WebSocket connection closed (attempt {reconnect_attempts}/{self.max_reconnect_attempts}): {e}"
                )
                if reconnect_attempts < self.max_reconnect_attempts:
                    logger.info(f"Reconnecting in {self.reconnect_delay} seconds...")
                    await asyncio.sleep(self.reconnect_delay)
                else:
                    logger.error("Max reconnect attempts reached")
                    yield TranscriptionResult(
                        text=full_text,
                        language=language,
                        success=False,
                        error="Connection lost after max reconnect attempts",
                    )
                    raise

            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                yield TranscriptionResult(
                    text=full_text,
                    language=language,
                    success=False,
                    error=str(e),
                )
                raise

    def _estimate_audio_duration(self, audio_bytes: bytes) -> float:
        """
        Estimate audio duration from raw bytes.

        Args:
            audio_bytes: Raw audio data

        Returns:
            Estimated duration in seconds
        """
        try:
            wav_file = io.BytesIO(audio_bytes)
            with wave.open(wav_file, "rb") as wav:
                frames = wav.getnframes()
                rate = wav.getframerate()
                if rate > 0:
                    return frames / rate
        except Exception as e:
            logger.debug(f"Could not estimate duration: {e}")

        return 5.0

    async def _mock_stream_transcribe(
        self,
        audio_generator: AsyncGenerator[bytes, None],
        language: Optional[str] = None,
    ) -> AsyncGenerator[TranscriptionResult, None]:
        """
        Mock streaming transcription for testing.

        Args:
            audio_generator: Async generator yielding audio chunks (ignored in mock mode)
            language: Optional source language code hint
            emit_policy: STT emission policy, either live or pause
            candidate_languages: Optional language candidates for detection

        Yields:
            Mock TranscriptionResult with incremental updates
        """
        mock_texts = {
            "en": [
                "Hello",
                ", how are you",
                " doing today",
                "? I would like to test",
                " the speech to text functionality",
                ".",
            ],
            "gu": [
                "હેલો",
                ", તમે કેમ છો",
                "? હું સ્પીચ ટુ ટેક્સ્ટ",
                " ફંક્શનલિટી ટેસ્ટ કરવા માંગું છું",
                ".",
            ],
            "hi": [
                "नमस्ते",
                ", आप कैसे हैं",
                "? मैं स्पीच टु टेक्स्ट",
                " फंक्शनलिटी टेस्ट करना चाहता हूं",
                ".",
            ],
        }

        detected_lang = self._normalize_language_hint(language) or "en"
        text_chunks = mock_texts.get(detected_lang, mock_texts["en"])

        full_text = ""
        for i, chunk in enumerate(text_chunks):
            full_text += chunk
            is_final = i == len(text_chunks) - 1

            yield TranscriptionResult(
                text=full_text,
                language=detected_lang,
                success=True,
                is_partial=not is_final,
            )

            if not is_final:
                await asyncio.sleep(0.5)

        logger.info(f"Mock streaming transcription complete: {full_text}")

    async def close(self) -> None:
        """
        Close the service and cleanup resources.
        """
        logger.info("WhisperService closed")
