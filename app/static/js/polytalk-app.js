// SPDX-FileCopyrightText: 2026 BizzAppDev Systems Pvt. Ltd.
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * PolyTalkApp Class
 * Main application class for PolyTalk
 */

class PolyTalkApp {
    /**
     * Main application class for PolyTalk
     */
    constructor() {
        this.audioRecorder = new AudioRecorder();
        this.recordedAudio = null;
        this.ws = null;
        this.isStreaming = false;
        this.isPaused = false;
        this.currentTranscript = '';
        this.currentTranslation = '';
        this.liveTranscriptText = '';
        this.liveTranslationText = '';
        this.autoPlayAudio = true;
        this.ttsEnabled = localStorage.getItem('polytalk_tts_enabled') !== 'false';
        this.ttsQueue = [];
        this.isPlayingTTS = false;
        this.currentTTSPreload = null;
        this.readinessStages = {};
        this.sessionStartMode = null;
        this.isMobileDevice = false;
        this.isTabAudioSupported = true;
        this.isApplyingLanguageParams = false;
        this.visualContextSent = false;

        this.initElements();
        this.applyLanguageParamsToView();
        this.initEventListeners();
        this.updateUIState('ready');
        this.initAsync();
    }

    /**
     * Cache for supported languages
     */
    supportedLanguages = null;

    /**
     * Translate a UI message key.
     */
    t(key, params = {}) {
        return window.PolyTalkI18n?.t(key, params) || key;
    }

    /**
     * Translate source text from the locale catalog.
     */
    uiText(value, params = {}) {
        return window.PolyTalkI18n?.text(value, params) || String(value ?? '');
    }

    /**
     * Return localized control button markup while preserving icon spans.
     */
    controlButtonHtml(iconClass, label) {
        return `<span class="control-icon ${iconClass}" aria-hidden="true"></span><span class="btn-text">${this.escapeHtml(label)}</span>`;
    }

    /**
     * Initialize async operations
     */
    async initAsync() {
        await this.checkBrowserSupport();
        this.checkMobileDevice();
        await this.loadSupportedLanguages();
    }

    /**
     * Initialize DOM elements
     */
    initElements() {
        this.liveBtn = document.getElementById('live-btn');
        this.conversationBtn = document.getElementById('conversation-btn');
        this.shareTabBtn = document.getElementById('share-tab-btn');
        this.recordingControls = document.getElementById('recording-controls');
        this.pauseBtn = document.getElementById('pause-btn');
        this.stopBtn = document.getElementById('stop-btn');
        this.sessionControls = document.getElementById('session-controls');
        this.swapLangsBtn = document.getElementById('swap-langs-btn');
        this.sourceLang = document.getElementById('source-lang');
        this.targetLang = document.getElementById('target-lang');
        this.microphoneSelect = document.getElementById('microphone-select');
        this.settingsBtn = document.getElementById('settings-btn');
        this.closeSettingsBtn = document.getElementById('close-settings-btn');
        this.settingsModal = document.getElementById('settings-modal');
        this.inputDeviceSelect = document.getElementById('input-device-select');
        this.outputDeviceSelect = document.getElementById('output-device-select');
        this.customInstructionInput = document.getElementById('custom-instruction-input');
        this.customInstructionCount = document.getElementById('custom-instruction-count');
        this.status = document.getElementById('status');
        this.recordingIndicator = document.getElementById('recording-indicator');
        this.transcript = document.getElementById('transcript');
        this.translation = document.getElementById('translation');
        this.audioContainer = document.getElementById('audio-container');
        this.audioPlayer = document.getElementById('audio-player');
        this.ttsEnabledSwitch = document.getElementById('tts-enabled-switch');
        this.ttsOutputStatus = document.getElementById('tts-output-status');

        if (this.ttsEnabledSwitch) {
            this.ttsEnabledSwitch.checked = this.ttsEnabled;
            this.ttsOutputStatus.textContent = this.ttsEnabled
                ? 'Voz activada'
                : 'Solo texto';

            this.ttsEnabledSwitch.addEventListener('change', () => {
                this.ttsEnabled = this.ttsEnabledSwitch.checked;
                localStorage.setItem('polytalk_tts_enabled', String(this.ttsEnabled));

                this.ttsOutputStatus.textContent = this.ttsEnabled
                    ? 'Voz activada'
                    : 'Solo texto';
            });
        }
        this.liveTranscript = document.getElementById('live-transcript');
        this.liveTranslation = document.getElementById('live-translation');
        this.readinessPanel = document.getElementById('readiness-panel');
        this.readinessSummary = document.getElementById('readiness-summary');
        this.readinessFlashTimer = null;

        // Enable buttons on init
        if (this.liveBtn) this.liveBtn.disabled = false;
        if (this.conversationBtn) this.conversationBtn.disabled = false;
        if (this.shareTabBtn) this.shareTabBtn.disabled = false;
        if (this.pauseBtn) this.pauseBtn.disabled = true;
        if (this.stopBtn) this.stopBtn.disabled = true;
        if (this.swapLangsBtn) {
            this.swapLangsBtn.disabled = false;
            this.swapLangsBtn.setAttribute('aria-disabled', 'false');
            this.swapLangsBtn.title = this.uiText('Swap Languages');
        }
        if (this.sessionControls) this.sessionControls.style.display = 'none';
    }

    /**
     * Initialize event listeners
     */
    initEventListeners() {
        this.liveBtn = document.getElementById('live-btn');
        if (this.liveBtn) {
            this.liveBtn.addEventListener('click', () => this.startLiveTranslation('live'));
        }
        this.conversationBtn = document.getElementById('conversation-btn');
        if (this.conversationBtn) {
            this.conversationBtn.addEventListener('click', () => this.startLiveTranslation('conversation'));
        }
        this.shareTabBtn = document.getElementById('share-tab-btn');
        if (this.shareTabBtn) {
            this.shareTabBtn.addEventListener('click', () => this.startShareTabAudio());
        }
        this.pauseBtn = document.getElementById('pause-btn');
        if (this.pauseBtn) {
            this.pauseBtn.addEventListener('click', () => this.togglePause());
        }
        this.stopBtn = document.getElementById('stop-btn');
        if (this.stopBtn) {
            this.stopBtn.addEventListener('click', () => this.stopRecording());
        }
        this.swapLangsBtn = document.getElementById('swap-langs-btn');
        if (this.swapLangsBtn) {
            this.swapLangsBtn.disabled = false;
            this.swapLangsBtn.setAttribute('aria-disabled', 'false');
            this.swapLangsBtn.addEventListener('click', () => this.handleSwapLanguages());
        }
        if (this.sourceLang) {
            this.sourceLang.addEventListener('change', () => this.handleLanguageChange());
        }
        if (this.targetLang) {
            this.targetLang.addEventListener('change', () => this.handleLanguageChange());
        }
        window.addEventListener('popstate', () => {
            try {
                this.applyLanguageParamsToView();
            } catch (error) {
                console.error('Failed to apply language params from browser history:', error);
            }
        });
        this.microphoneSelect = document.getElementById('microphone-select');
        if (this.microphoneSelect) {
            this.microphoneSelect.addEventListener('change', (e) => this.handleMicrophoneChange(e));
        }
        this.settingsBtn = document.getElementById('settings-btn');
        if (this.settingsBtn) {
            this.settingsBtn.addEventListener('click', () => {
                this.openSettings();
            });
        }
        this.closeSettingsBtn = document.getElementById('close-settings-btn');
        if (this.closeSettingsBtn) {
            this.closeSettingsBtn.addEventListener('click', () => this.closeSettings());
        }
        if (this.settingsModal) {
            this.settingsModal.addEventListener('click', (e) => {
                if (e.target === this.settingsModal) {
                    this.closeSettings();
                }
            });
        }
        this.inputDeviceSelect = document.getElementById('input-device-select');
        if (this.inputDeviceSelect) {
            this.inputDeviceSelect.addEventListener('change', (e) => this.handleInputDeviceChange(e));
        }
        this.outputDeviceSelect = document.getElementById('output-device-select');
        if (this.outputDeviceSelect) {
            this.outputDeviceSelect.addEventListener('change', (e) => this.handleOutputDeviceChange(e));
        }
        if (this.customInstructionInput) {
            this.customInstructionInput.addEventListener('input', () => this.updateCustomInstructionCount());
            this.customInstructionInput.addEventListener('paste', () => {
                window.setTimeout(() => this.enforceCustomInstructionMaxLength(), 0);
            });
            this.customInstructionInput.addEventListener('drop', () => {
                window.setTimeout(() => this.enforceCustomInstructionMaxLength(), 0);
            });
        }
        const saveSettingsBtn = document.getElementById('save-settings-btn');
        if (saveSettingsBtn) {
            saveSettingsBtn.addEventListener('click', () => this.saveSettings());
        }
    }

    /**
     * Handle source/target language dropdown changes.
     */
    handleLanguageChange() {
        if (this.isApplyingLanguageParams) {
            return;
        }
        this.updateLanguageParamsInUrl();
    }

    /**
     * Apply source and target language query parameters to the dropdowns.
     */
    applyLanguageParamsToView() {
        if (this.isApplyingLanguageParams || !this.sourceLang || !this.targetLang) {
            return;
        }

        this.isApplyingLanguageParams = true;
        try {
            const params = new URLSearchParams(window.location.search);
            const sourceLanguage = this.getFirstQueryParam(params, [
                'source_language',
                'source_lang',
                'source'
            ]);
            const targetLanguage = this.getFirstQueryParam(params, [
                'target_language',
                'target_lang',
                'target'
            ]);

            let appliedParam = false;
            if (sourceLanguage && this.isEnabledLanguageOption(this.sourceLang, sourceLanguage)) {
                this.sourceLang.value = sourceLanguage;
                appliedParam = true;
            }
            if (targetLanguage && this.isEnabledLanguageOption(this.targetLang, targetLanguage)) {
                this.targetLang.value = targetLanguage;
                appliedParam = true;
            }

            if (appliedParam) {
                this.updateLanguageParamsInUrl({ replace: true });
            }
        } finally {
            this.isApplyingLanguageParams = false;
        }
    }

    /**
     * Return the first present query parameter value from a list of names.
     * @param {URLSearchParams} params - Current query params
     * @param {Array<string>} names - Accepted parameter names
     * @returns {string|null} - First matching parameter value
     */
    getFirstQueryParam(params, names) {
        for (const name of names) {
            const value = params.get(name);
            if (value) {
                return value;
            }
        }
        return null;
    }

    /**
     * Check whether a language code exists as an enabled option in a select.
     * @param {HTMLSelectElement} select - Language select element
     * @param {string} languageCode - Language code from URL or UI
     * @returns {boolean} - Whether the option can be selected
     */
    isEnabledLanguageOption(select, languageCode) {
        return Array.from(select.options).some(
            option => option.value === languageCode && !option.disabled
        );
    }

    /**
     * Get the currently selected source and target language values.
     * @returns {Object} - Current language pair
     */
    getCurrentLanguagePair() {
        return {
            sourceLanguage: this.sourceLang?.value || '',
            targetLanguage: this.targetLang?.value || ''
        };
    }

    /**
     * Keep source and target language query params in sync with the current UI.
     * @param {Object} options - Sync options
     * @param {boolean} options.replace - Replace current history entry instead of adding one
     */
    updateLanguageParamsInUrl({ replace = true } = {}) {
        if (!this.sourceLang || !this.targetLang) {
            return;
        }

        const url = new URL(window.location.href);
        const { sourceLanguage, targetLanguage } = this.getCurrentLanguagePair();

        // Normalize supported legacy aliases to the canonical parameter names.
        url.searchParams.delete('source_lang');
        url.searchParams.delete('target_lang');
        url.searchParams.delete('source');
        url.searchParams.delete('target');

        if (sourceLanguage) {
            url.searchParams.set('source_language', sourceLanguage);
        } else {
            url.searchParams.delete('source_language');
        }

        if (targetLanguage) {
            url.searchParams.set('target_language', targetLanguage);
        } else {
            url.searchParams.delete('target_language');
        }

        if (url.href === window.location.href) {
            return;
        }

        const historyMethod = replace ? 'replaceState' : 'pushState';
        window.history[historyMethod]({}, '', url);
    }



    /**
     * Handle microphone selection change
     * @param {Event} event - Change event
     */
    handleMicrophoneChange(event) {
        const deviceId = event.target.value;
        this.audioRecorder.setDeviceId(deviceId);
        this.showStatus(this.t('js.microphone_changed', { device: event.target.options[event.target.selectedIndex].text }), 'info');
        if (this.inputDeviceSelect) {
            this.inputDeviceSelect.value = deviceId;
        }
    }

    /**
     * Handle input device selection change (from settings modal)
     * @param {Event} event - Change event
     */
    handleInputDeviceChange(event) {
        const deviceId = event.target.value;
        this.audioRecorder.setDeviceId(deviceId);
        this.showStatus(this.t('js.microphone_changed', { device: event.target.options[event.target.selectedIndex].text }), 'info');
        if (this.microphoneSelect) {
            this.microphoneSelect.value = deviceId;
        }
    }

    /**
     * Handle output device selection change
     * @param {Event} event - Change event
     */
    handleOutputDeviceChange(event) {
        const deviceId = event.target.value;
        this.audioRecorder.setOutputDeviceId(deviceId);
        this.applyOutputDeviceToAudioPlayer(deviceId);
        this.showStatus(this.t('js.output_device_changed', { device: event.target.options[event.target.selectedIndex].text }), 'info');
    }

    /**
     * Apply output device to audio player element
     * @param {string} deviceId - Device ID
     */
    applyOutputDeviceToAudioPlayer(deviceId) {
        this.applyOutputDeviceToAudioElement(this.audioPlayer, deviceId);
    }

    /**
     * Apply output device to any audio element
     * @param {HTMLAudioElement} audioElement - Audio element
     * @param {string} deviceId - Device ID
     */
    applyOutputDeviceToAudioElement(audioElement, deviceId) {
        if (audioElement && deviceId && deviceId !== 'default' && audioElement.setSinkId) {
            audioElement.setSinkId(deviceId).catch(err => {
                console.error('Failed to set audio output device:', err);
            });
        }
    }

    /**
     * Open settings modal
     */

    async createOmniVoiceProfile() {
        const nameInput = document.getElementById('clone-voice-name');
        const fileInput = document.getElementById('clone-voice-file');
        const button = document.getElementById('clone-voice-create');
        const status = document.getElementById('clone-voice-status');

        const name = nameInput?.value.trim() || '';
        const file = fileInput?.files?.[0];

        if (!name) {
            if (status) status.textContent = 'Ingresa un nombre para la voz.';
            return;
        }

        if (!file) {
            if (status) status.textContent = 'Selecciona un archivo de audio.';
            return;
        }

        if (file.size > 20 * 1024 * 1024) {
            if (status) status.textContent = 'El audio supera los 20 MB.';
            return;
        }

        const allowed = ['wav', 'mp3', 'ogg', 'webm', 'm4a'];
        const extension = file.name.split('.').pop().toLowerCase();

        if (!allowed.includes(extension)) {
            if (status) status.textContent = 'Formato de audio no permitido.';
            return;
        }

        const data = new FormData();
        data.append('name', name);
        data.append('audio_file', file);

        if (button) button.disabled = true;
        if (status) status.textContent = 'Creando perfil de voz...';

        try {
            const response = await fetch('/api/voices/custom/create', {
                method: 'POST',
                body: data
            });

            if (!response.ok) {
                let detail = `HTTP ${response.status}`;
                try {
                    const errorData = await response.json();
                    detail = errorData.detail || detail;
                } catch (_) {}
                throw new Error(detail);
            }

            const result = await response.json();
            console.log('OmniVoice profile created:', result);

            await this.loadOmniVoiceProfiles();

            if (status) {
                status.textContent = 'Perfil de voz creado correctamente.';
            }

            if (fileInput) fileInput.value = '';

        } catch (error) {
            console.error('Voice creation failed:', error);

            if (status) {
                status.textContent = `Error al crear voz: ${error.message}`;
            }
        } finally {
            if (button) button.disabled = false;
        }
    }

    async loadOmniVoiceProfiles() {
        const select = document.getElementById('clone-voice-select');
        const status = document.getElementById('clone-voice-status');

        if (!select) return;

        const selectedVoice = select.value;

        try {
            const response = await fetch('/api/voices/my-list', {
                cache: 'no-store'
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const result = await response.json();
            const voices = Array.isArray(result)
                ? result
                : (Array.isArray(result.voices) ? result.voices : []);

            select.replaceChildren(new Option('Voz predeterminada', ''));

            for (const voice of voices) {
                const voiceId = voice.voice_id ?? voice.id;
                if (voiceId === undefined || voiceId === null) continue;

                const voiceName = voice.name
                    ?? voice.display_name
                    ?? String(voiceId);

                select.add(new Option(
                    String(voiceName),
                    String(voiceId)
                ));
            }

            if (Array.from(select.options).some(
                option => option.value === selectedVoice
            )) {
                select.value = selectedVoice;
            }

            if (status) {
                status.textContent = voices.length
                    ? `${voices.length} voz(es) disponible(s).`
                    : 'Todav?a no hay voces guardadas en OmniVoice.';
            }
        } catch (error) {
            console.error('Error loading OmniVoice profiles:', error);

            if (status) {
                status.textContent =
                    'No se pudo consultar OmniVoice. Intenta nuevamente.';
            }
        }

        const voiceSelect = document.getElementById('clone-voice-select');

        if (voiceSelect) {
            const savedVoiceId =
                localStorage.getItem('polytalk_omnivoice_voice_id');

            if (savedVoiceId) {
                const exists = Array.from(voiceSelect.options)
                    .some(option => option.value === savedVoiceId);

                if (exists) {
                    voiceSelect.value = savedVoiceId;
                }
            }

            voiceSelect.onchange = () => {
                localStorage.setItem(
                    'polytalk_omnivoice_voice_id',
                    voiceSelect.value
                );
            };

            if (voiceSelect.value) {
                localStorage.setItem(
                    'polytalk_omnivoice_voice_id',
                    voiceSelect.value
                );
            }
        }
    }


    async testOmniVoiceProfile() {
        const select = document.getElementById('clone-voice-select');
        const button = document.getElementById('clone-voice-test');
        const status = document.getElementById('clone-voice-status');
        const player = document.getElementById('clone-audio-preview');

        const voiceId = select?.value || '';

        if (!voiceId) {
            if (status) status.textContent = 'Selecciona una voz guardada.';
            return;
        }

        const textInput = document.getElementById('clone-test-text');
        const text = textInput?.value.trim() || '';

        if (!text) {
            if (status) {
                status.textContent = 'Escribe una frase para probar la voz.';
            }
            return;
        }

        if (text.length > 500) {
            if (status) {
                status.textContent = 'La frase no puede superar los 500 caracteres.';
            }
            return;
        }

        if (button) button.disabled = true;
        if (status) status.textContent = 'Generando audio con OmniVoice...';

        try {
            const response = await fetch('/api/voices/synthesize', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    text: text,
                    voice_id: voiceId
                })
            });

            const result = await response.json();

            if (!response.ok || !result.success) {
                throw new Error(
                    result.detail || 'No fue posible generar el audio.'
                );
            }

            if (!result.audio_url || !player) {
                throw new Error('No se encontro el reproductor de audio.');
            }

            if (!/^\/api\/audio\/[A-Za-z0-9_-]+\.wav$/.test(result.audio_url)) {
                throw new Error('La URL del audio no es valida.');
            }

            player.pause();
            player.src = result.audio_url;
            player.style.display = 'block';
            player.load();

            if (status) {
                status.textContent = 'Audio generado. Reproduciendo prueba de voz...';
            }

            try {
                await player.play();
            } catch (playError) {
                if (status) {
                    status.textContent = 'Audio listo. Presiona Play para escucharlo.';
                }
            }

        } catch (error) {
            if (status) {
                status.textContent = 'Error: ' + error.message;
            }
        } finally {
            if (button) button.disabled = false;
        }
    }

    async openSettings() {
        if (this.settingsModal) {
            this.settingsModal.classList.add('active');

            const cloneCreateButton = document.getElementById('clone-voice-create');
            if (cloneCreateButton && !cloneCreateButton.dataset.voiceCreateBound) {
                cloneCreateButton.addEventListener('click', () => {
                    void this.createOmniVoiceProfile();
                });
                cloneCreateButton.dataset.voiceCreateBound = 'true';
            }

            const cloneTestButton = document.getElementById('clone-voice-test');
            if (cloneTestButton && !cloneTestButton.dataset.voiceTestBound) {
                cloneTestButton.addEventListener('click', () => {
                    void this.testOmniVoiceProfile();
                });
                cloneTestButton.dataset.voiceTestBound = 'true';
            }

            void this.loadOmniVoiceProfiles();

            // Request microphone permission first to ensure all devices are visible
            const hasPermission = await this.audioRecorder.requestPermission();
            if (!hasPermission) {
                alert(this.uiText('Microphone permission is required to view available devices.'));
                return;
            }

            await this.loadInputDevices();
            await this.loadOutputDevices();
            this.loadSavedDevicePreferences();
        } else {
            console.error('Settings modal not found in DOM!');
        }
    }

    /**
     * Close settings modal
     */
    closeSettings() {
        if (this.settingsModal) {
            this.settingsModal.classList.remove('active');
        }
    }

    /**
     * Save settings and close modal
     */
    saveSettings() {
        const inputDeviceId = this.inputDeviceSelect?.value || 'default';
        const outputDeviceId = this.outputDeviceSelect?.value || 'default';

        const customInstruction = this.getCustomInstruction();
        if (this.customInstructionInput) {
            this.customInstructionInput.value = customInstruction;
            this.updateCustomInstructionCount();
        }

        localStorage.setItem('polytalk_input_device', inputDeviceId);
        localStorage.setItem('polytalk_output_device', outputDeviceId);
        localStorage.setItem('polytalk_custom_instruction', customInstruction);
        const ttsEngine = document.getElementById('tts-engine-select')?.value || 'xtts';
        localStorage.setItem('polytalk_tts_engine', ttsEngine);

        this.audioRecorder.selectedDeviceId = inputDeviceId;
        this.audioRecorder.setOutputDeviceId(outputDeviceId);

        if (this.microphoneSelect) {
            this.microphoneSelect.value = inputDeviceId;
        }

        this.applyOutputDeviceToAudioPlayer(outputDeviceId);

        this.showStatus(this.uiText('Settings saved successfully!'), 'success');
        this.closeSettings();
    }

    /**
     * Load saved device preferences
     */
    loadSavedDevicePreferences() {
        const savedInputDevice = localStorage.getItem('polytalk_input_device');
        const savedOutputDevice = localStorage.getItem('polytalk_output_device');
        this.loadSavedCustomInstruction();
        const ttsEngineSelect = document.getElementById('tts-engine-select');
        const savedTtsEngine = localStorage.getItem('polytalk_tts_engine') || 'xtts';
        if (ttsEngineSelect && Array.from(ttsEngineSelect.options).some(option => option.value === savedTtsEngine)) {
            ttsEngineSelect.value = savedTtsEngine;
        }

        if (savedInputDevice) {
            this.audioRecorder.selectedDeviceId = savedInputDevice;
        }

        if (savedOutputDevice) {
            this.audioRecorder.selectedOutputDeviceId = savedOutputDevice;
            this.applyOutputDeviceToAudioPlayer(savedOutputDevice);
        }
    }

    /**
     * Load saved custom translation instruction into settings.
     */
    loadSavedCustomInstruction() {
        if (!this.customInstructionInput) return;

        this.customInstructionInput.value = localStorage.getItem('polytalk_custom_instruction') || '';
        this.enforceCustomInstructionMaxLength();
    }

    /**
     * Return normalized custom instruction text bounded by the textarea limit.
     * @returns {string} - Saved custom instruction
     */
    getCustomInstruction() {
        if (!this.customInstructionInput) return '';

        return this.normalizeCustomInstruction(
            this.customInstructionInput.value,
            this.getCustomInstructionMaxLength()
        );
    }

    /**
     * Normalize custom instruction text and optionally bound its length.
     * @param {string} value - Raw instruction text
     * @param {number|null} maxLength - Maximum character length
     * @returns {string} - Normalized instruction text
     */
    normalizeCustomInstruction(value, maxLength = null) {
        // Keep this logic aligned with app/utils/sanitize.py normalize_instruction().
        const instruction = String(value || '')
            .replace(/[\x00-\x1f\x7f]/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();

        return maxLength ? instruction.slice(0, maxLength) : instruction;
    }

    /**
     * Enforce configured custom instruction length after non-typing edits.
     */
    enforceCustomInstructionMaxLength() {
        if (!this.customInstructionInput) return;

        const maxLength = this.getCustomInstructionMaxLength();
        if (maxLength && this.customInstructionInput.value.length > maxLength) {
            this.customInstructionInput.value = this.customInstructionInput.value.slice(0, maxLength);
        }
        this.updateCustomInstructionCount();
    }

    /**
     * Return the configured custom instruction length from the rendered textarea.
     * @returns {number|null} - Maximum length, or null when not configured
     */
    getCustomInstructionMaxLength() {
        if (!this.customInstructionInput) return null;

        const maxLength = Number.parseInt(
            this.customInstructionInput.getAttribute('maxlength'),
            10
        );
        return Number.isFinite(maxLength) && maxLength > 0 ? maxLength : null;
    }

    /**
     * Update custom instruction character count in settings.
     */
    updateCustomInstructionCount() {
        if (!this.customInstructionInput || !this.customInstructionCount) return;

        const maxLength = this.getCustomInstructionMaxLength();
        const currentLength = this.customInstructionInput.value.length;
        this.customInstructionCount.textContent = maxLength
            ? `${currentLength}/${maxLength}`
            : `${currentLength}`;
    }

    /**
     * Build a translation WebSocket URL with optional custom instruction.
     * @param {string} sourceLang - Source language code
     * @param {string} targetLang - Target language code
     * @param {string} mode - Translation mode
     * @returns {string} - WebSocket URL
     */
    buildTranslationWebSocketUrl(sourceLang, targetLang, mode) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const params = new URLSearchParams({
            source_language: sourceLang,
            target_language: targetLang,
            mode
        });
        const sttEngine = document.getElementById('stt-engine-select')?.value === 'parakeet'
            ? 'parakeet'
            : 'whisper';
        params.set('stt_engine', sttEngine);
        const ttsEngine = localStorage.getItem('polytalk_tts_engine') || 'xtts';
        params.set('tts_engine', ttsEngine);

        if (ttsEngine === 'omnivoice') {
            const selectedVoiceId =
                localStorage.getItem('polytalk_omnivoice_voice_id');

            if (selectedVoiceId) {
                params.set('voice_id', selectedVoiceId);
            }
        }

        params.set('tts_enabled', String(this.ttsEnabled));

        const customInstruction = this.getCustomInstruction();
        if (customInstruction) {
            params.set('custom_instruction', customInstruction);
        }

        return `${protocol}//${window.location.host}/api/ws/translate?${params.toString()}`;
    }

    /**
     * Load available input devices (microphones)
     * Assumes permission has already been granted in openSettings()
     */
    async loadInputDevices() {
        if (!this.inputDeviceSelect) return;

        // Get saved device before rebuilding the list
        const savedDevice = localStorage.getItem('polytalk_input_device');

        const devices = await this.audioRecorder.enumerateDevices();

        this.inputDeviceSelect.innerHTML = '';

        devices.forEach((device, index) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.textContent = device.label || this.t('js.microphone_number', { number: index + 1 });
            this.inputDeviceSelect.appendChild(option);
        });

        // Restore saved selection if still available
        if (savedDevice) {
            const optionExists = Array.from(this.inputDeviceSelect.options).some(
                opt => opt.value === savedDevice
            );
            if (optionExists) {
                this.inputDeviceSelect.value = savedDevice;
            }
        }
    }

    /**
     * Load available output devices
     * Assumes permission has already been granted in openSettings()
     */
    async loadOutputDevices() {
        if (!this.outputDeviceSelect) return;

        // Get saved device before rebuilding the list
        const savedDevice = localStorage.getItem('polytalk_output_device');

        const devices = await this.audioRecorder.enumerateOutputDevices();
        this.outputDeviceSelect.innerHTML = '';

        devices.forEach((device, index) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.textContent = device.label || this.t('js.speaker_number', { number: index + 1 });
            this.outputDeviceSelect.appendChild(option);
        });

        // Restore saved selection if still available
        if (savedDevice) {
            const optionExists = Array.from(this.outputDeviceSelect.options).some(
                opt => opt.value === savedDevice
            );
            if (optionExists) {
                this.outputDeviceSelect.value = savedDevice;
            }
        }
    }

    /**
     * Handle swap languages button click
     */
    async handleSwapLanguages() {
        if (this.isStreaming || this.ws?.readyState === WebSocket.OPEN) {
            this.showStatus(this.uiText('Swap is disabled during active sessions.'), 'warning');
            return;
        }
        const {
            sourceLanguage: sourceValue,
            targetLanguage: targetValue
        } = this.getCurrentLanguagePair();

        if (sourceValue === targetValue) {
            this.showStatus(this.uiText('Source and target languages must be different.'), 'warning');
            return;
        }

        this.sourceLang.value = targetValue;
        this.targetLang.value = sourceValue;
        this.updateLanguageParamsInUrl();
        this.showStatus(this.uiText('Languages swapped!'), 'info');
    }

    /**
     * Check if device is mobile and disable tab audio accordingly
     */
    checkMobileDevice() {
        this.isMobileDevice =
            window.innerWidth <= 768 || /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
        this.updateShareTabAvailability();
    }

    /**
     * Update the Share Tab Audio button based on browser and device support.
     */
    updateShareTabAvailability() {
        if (!this.shareTabBtn) {
            return;
        }

        const disabled = this.isMobileDevice || !this.isTabAudioSupported;
        this.shareTabBtn.disabled = disabled;
        if (this.isMobileDevice) {
            this.shareTabBtn.title = this.uiText('Not supported on mobile yet');
        } else if (!this.isTabAudioSupported) {
            this.shareTabBtn.title = this.uiText('Tab audio sharing is not supported in this browser');
        } else {
            this.shareTabBtn.title = this.uiText('Share Tab Audio');
        }
    }

    /**
     * Check if browser supports audio recording
     */
    async checkBrowserSupport() {
        this.isTabAudioSupported = AudioRecorder.isTabAudioSupported();

        if (!AudioRecorder.isSupported()) {
            this.showStatus(
                this.uiText('Your browser does not support audio recording. Please use a modern browser like Chrome, Firefox, or Edge.'),
                'error'
            );
            if (this.liveBtn) this.liveBtn.disabled = true;
        }
        if (!this.isTabAudioSupported) {
            this.showStatus(
                this.uiText('Your browser does not support tab audio sharing.'),
                'warning'
            );
        }

        // Check if we already have permission
        const permission = await navigator.permissions.query({ name: 'microphone' });

        if (permission.state === 'granted') {
            // Permission already granted, load microphones
            await this.loadMicrophones();
            this.showStatus(this.uiText('Ready to use microphone for live translation'), 'success');
        } else if (permission.state === 'denied') {
            // Permission denied
            this.showStatus(
                this.uiText('Microphone permission denied. Click the lock icon in your browser address bar and allow microphone access to use live translation.'),
                'error'
            );
            if (this.liveBtn) this.liveBtn.disabled = true;
        } else {
            // Need to request permission
            this.showStatus(
                this.uiText('Requesting microphone access...'),
                'info'
            );

            // Auto-request permission
            const hasPermission = await this.audioRecorder.requestPermission();
            if (hasPermission) {
                await this.loadMicrophones();
                this.showStatus(this.uiText('Microphone access granted! Ready for live translation.'), 'success');
                if (this.liveBtn) this.liveBtn.disabled = false;
            } else {
                this.showStatus(
                    this.uiText('Microphone access needed. Click "Live Translate" and allow microphone permission when prompted.'),
                    'info'
                );
                if (this.liveBtn) this.liveBtn.disabled = false;
            }
        }

        // Enable buttons by default
        if (this.liveBtn) this.liveBtn.disabled = false;
        this.updateShareTabAvailability();
        if (this.stopBtn) this.stopBtn.disabled = true;
    }

    /**
     * Load available microphones
     */
    async loadMicrophones() {
        if (!this.microphoneSelect) return;

        const devices = await this.audioRecorder.enumerateDevices();
        this.microphoneSelect.innerHTML = '';
        const defaultOption = document.createElement('option');
        defaultOption.value = '';
        defaultOption.textContent = this.uiText('Select Microphone');
        this.microphoneSelect.appendChild(defaultOption);

        devices.forEach((device, index) => {
            const option = document.createElement('option');
            option.value = device.deviceId;
            option.textContent = device.label || this.t('js.microphone_number', { number: index + 1 });
            this.microphoneSelect.appendChild(option);
        });
    }

    /**
     * Reset the visible session readiness panel.
     * @param {string} summary - Short readiness summary
     */
    resetReadinessPanel(summary = this.uiText('Preparing session')) {
        this.readinessStages = {};
        this.preserveReadinessNotice = false;
        this.flashReadiness(summary, 'active', false);
    }

    /**
     * Flash a compact readiness update.
     * @param {string} message - Short readiness message
     * @param {string} status - active, done, warning, or error
     * @param {boolean} autoHide - Whether to hide the flash after a short delay
     */
    flashReadiness(message, status = 'active', autoHide = true) {
        if (this.readinessFlashTimer) {
            clearTimeout(this.readinessFlashTimer);
            this.readinessFlashTimer = null;
        }
        if (this.readinessPanel) {
            this.readinessPanel.classList.remove('active', 'done', 'warning', 'error');
            this.readinessPanel.classList.add('active');
            if (status !== 'active') {
                this.readinessPanel.classList.add(status);
            }
        }
        if (this.readinessSummary) {
            this.readinessSummary.textContent = message;
        }
        if (autoHide && status !== 'error') {
            this.readinessFlashTimer = setTimeout(() => this.setReadinessIdle(), 1300);
        }
    }

    /**
     * Return readiness UI to its idle state.
     */
    setReadinessIdle() {
        this.readinessStages = {};
        this.preserveReadinessNotice = false;
        if (this.readinessFlashTimer) {
            clearTimeout(this.readinessFlashTimer);
            this.readinessFlashTimer = null;
        }
        if (this.readinessPanel) {
            this.readinessPanel.classList.remove('active', 'done', 'warning', 'error');
        }
        if (this.readinessSummary) {
            this.readinessSummary.textContent = this.uiText('Ready');
        }
    }

    /**
     * Update one readiness stage in the startup panel.
     * @param {string} stage - Stage identifier
     * @param {string} status - pending, active, done, warning, or error
     * @param {string} message - Short summary for the panel header
     */
    updateReadinessStage(stage, status, message = '') {
        this.readinessStages[stage] = status;
        if (message && status !== 'pending') {
            this.flashReadiness(message, status, false);
        }
        this.maybeMarkSessionListening();
    }

    /**
     * Mark the session as listening once capture and backend readiness are done.
     */
    maybeMarkSessionListening() {
        const hasAudio = this.readinessStages.audio_capture === 'done';
        const hasServer = this.readinessStages.server_connected === 'done';
        const hasPipeline = ['done', 'warning'].includes(
            this.readinessStages.pipeline_ready
        );
        if (!hasAudio || !hasServer || !hasPipeline) {
            return;
        }

        this.readinessStages.listening = 'done';
        this.flashReadiness(this.uiText('Ready. Listening now.'), 'done', true);
        this.isStreaming = true;
        this.isPaused = false;
        this.updateUIState('streaming');
        this.scrollToStatusMessage();
    }

    /**
     * Bring the status/readiness area into view on small screens.
     */
    scrollToStatusMessage() {
        if (!this.status) {
            return;
        }
        if (!window.matchMedia('(max-width: 768px)').matches) {
            return;
        }

        window.requestAnimationFrame(() => {
            this.status.scrollIntoView({
                behavior: 'smooth',
                block: 'start',
                inline: 'nearest'
            });
        });
    }

    /**
     * Handle backend connection and pipeline readiness updates.
     * @param {Object} result - Pipeline status result
     */
    handlePipelineStatus(result) {
        const stage = result.stage;
        const status = result.status || 'active';
        const message = result.message ? this.uiText(result.message) : this.uiText('Preparing session');

        if (stage === 'pipeline_warming') {
            this.updateReadinessStage('pipeline_ready', 'active', message);
            return;
        }

        this.updateReadinessStage(stage, status, message);

        if (status === 'error' || status === 'warning') {
            const log = status === 'error' ? console.error : console.warn;
            log(`Pipeline ${stage} ${status}: ${message}`);
        }
    }

    /**
     * Start live translation with microphone
     */
    async startLiveTranslation(translationMode = 'live') {
        const {
            sourceLanguage: sourceLang,
            targetLanguage: targetLang
        } = this.getCurrentLanguagePair();

        if (sourceLang === targetLang) {
            this.showStatus(this.uiText('Source and target languages must be different.'), 'warning');
            return;
        }

        this.sessionStartMode = 'microphone';
        this.resetReadinessPanel(this.uiText('Requesting microphone access'));
        this.updateReadinessStage('audio_permission', 'active', this.uiText('Requesting microphone access'));
        this.updateUIState('connecting');
        this.clearLiveResults();

        const wsUrl = this.buildTranslationWebSocketUrl(sourceLang, targetLang, translationMode);

        this.updateReadinessStage('server_connected', 'active', this.uiText('Connecting to server'));
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this.updateReadinessStage('server_connected', 'done', this.uiText('Server connected'));
        };

        this.ws.onmessage = (event) => {
            const result = JSON.parse(event.data);
            this.handleStreamResult(result);
        };

        this.ws.onerror = (error) => {
            this.showSessionNotice(this.uiText('Connection issue detected. Please try again.'), 'error');
            this.closeWebSocket();
        };

        this.ws.onclose = () => {
            this.isStreaming = false;
            this.isPaused = false;
            this.updateUIState('ready');
        };

        const suppressSilentChunks = translationMode === 'conversation';
        const success = await this.audioRecorder.startMicrophone((chunk, level = 0) => {
            if (suppressSilentChunks && level <= 0.002) {
                return;
            }
            if (this.ws && this.ws.readyState === WebSocket.OPEN && !this.isPaused) {
                this.ws.send(chunk);
            }
        });

        if (!success) {
            this.showSessionNotice(this.uiText('Failed to start microphone. Please check permissions.'), 'error');
            this.updateReadinessStage('audio_permission', 'error', this.uiText('Microphone unavailable'));
            this.closeWebSocket();
            this.updateUIState('ready');
            return;
        }

        this.updateReadinessStage('audio_permission', 'done', this.uiText('Microphone ready'));
        this.updateReadinessStage('audio_capture', 'done', this.uiText('Audio capture ready'));
    }

    /**
     * Handle streaming result from WebSocket
     */
    handleStreamResult(result) {
        if (result.type === 'swap_disabled') {
            this.showStatus(result.message ? this.uiText(result.message) : this.uiText('Swap is disabled in live and conversation modes.'), 'warning');
            return;
        }
        if (result.type === 'conversation_turn') {
            this.handleConversationTurn(result);
            return;
        }
        if (result.type === 'pipeline_status') {
            this.handlePipelineStatus(result);
            return;
        }

        switch (result.type) {
            case 'transcription':
                if (result.transcript) {
                    // Backend streaming transcription is cumulative.
                    this.liveTranscriptText = result.transcript;
                }
                this.updateLiveTranscript(this.liveTranscriptText, result.is_partial);
                break;

            case 'translation':
                if (result.translated_text) {
                    // Backend sends full accumulated translation, so we replace
                    this.liveTranslationText = result.translated_text;
                }
                this.updateLiveTranslation(this.liveTranslationText);
                break;

            case 'tts':
                if (result.audio_url) {
                    // Add sequence number for ordering (default to current queue length)
                    const sequence = result.sequence !== undefined ? result.sequence : this.ttsQueue.length;
                    this.ttsQueue.push(this.createTTSQueueItem(result.audio_url, sequence));
                    if (!this.isPlayingTTS) {
                        this.playNextTTS();
                    }
                }
                break;

            case 'complete':
                this.showSessionNotice(this.uiText('Live translation complete!'), 'success');
                if (result.transcript) {
                    this.transcript.innerHTML = this.escapeHtml(result.transcript);
                }
                if (result.translated_text) {
                    this.translation.innerHTML = this.escapeHtml(result.translated_text);
                }
                setTimeout(() => {
                    this.updateUIState('ready');
                }, 2000);
                break;

            case 'error':
                this.showSessionNotice(this.t('js.error_message', { error: result.error }), 'error');
                this.closeWebSocket();
                break;
        }
    }

    /**
     * Play next TTS in queue (sorted by sequence number)
     */
    async playNextTTS() {
        if (this.ttsQueue.length === 0) {
            this.isPlayingTTS = false;
            this.cleanupTTSPreload(this.currentTTSPreload);
            this.currentTTSPreload = null;
            return;
        }

        // Sort queue by sequence number to ensure correct order
        this.ttsQueue.sort((a, b) => a.sequence - b.sequence);

        this.isPlayingTTS = true;
        const item = this.ttsQueue.shift();
        const audioUrl = item.url;
        this.cleanupTTSPreload(this.currentTTSPreload);
        this.currentTTSPreload = item.audio;

        this.audioPlayer.src = audioUrl;
        this.audioContainer.style.display = 'block';
        this.audioPlayer.load();

        try {
            await this.audioPlayer.play();
            // Wait for audio to finish playing
            await new Promise(resolve => {
                const done = () => resolve();
                this.audioPlayer.addEventListener('ended', done, { once: true });
                this.audioPlayer.addEventListener('error', done, { once: true });
            });
        } catch (err) {
            console.warn('TTS playback error:', err);
        } finally {
            if (this.currentTTSPreload === item.audio) {
                this.cleanupTTSPreload(this.currentTTSPreload);
                this.currentTTSPreload = null;
            }
        }

        // Play next in queue
        this.playNextTTS();
    }

    /**
     * Create a queued TTS item and begin loading it immediately.
     */
    createTTSQueueItem(audioUrl, sequence) {
        const audio = new Audio(audioUrl);
        audio.preload = 'auto';
        this.applyOutputDeviceToAudioElement(audio, this.audioRecorder.selectedOutputDeviceId);
        audio.load();
        return { url: audioUrl, sequence: sequence, audio: audio };
    }

    /**
     * Stop a preloaded TTS audio element and release its media resource.
     */
    cleanupTTSPreload(audio) {
        if (!audio) {
            return;
        }
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
    }

    /**
     * Clear queued and active TTS playback.
     */
    resetTTSPlayback() {
        this.ttsQueue.forEach(item => {
            if (item.audio !== this.currentTTSPreload) {
                this.cleanupTTSPreload(item.audio);
            }
        });
        this.ttsQueue = [];
        this.isPlayingTTS = false;
        this.cleanupTTSPreload(this.currentTTSPreload);
        this.currentTTSPreload = null;
        if (this.audioPlayer) {
            this.audioPlayer.pause();
            this.audioPlayer.removeAttribute('src');
            this.audioPlayer.load();
        }
    }

    handleConversationTurn(result) {
        const source = this.getLanguageName(result.source_language);
        const target = this.getLanguageName(result.target_language);
        const direction = `${source} -> ${target}`;
        const transcript = this.escapeHtml(result.transcript || '');
        const translation = this.escapeHtml(result.translated_text || '');
        if (this.liveTranscript) {
            this.liveTranscript.querySelector('.placeholder')?.remove();
            this.liveTranscript.insertAdjacentHTML(
                'beforeend',
                `<div class="conversation-turn"><strong>${this.escapeHtml(direction)}</strong><p>${transcript}</p></div>`
            );
        }
        if (this.liveTranslation) {
            this.liveTranslation.querySelector('.placeholder')?.remove();
            this.liveTranslation.insertAdjacentHTML(
                'beforeend',
                `<div class="conversation-turn"><strong>${this.escapeHtml(direction)}</strong><p>${translation}</p></div>`
            );
        }
    }

    /**
     * Update live transcript display
     */
    updateLiveTranscript(text, isPartial) {
        if (this.liveTranscript) {
            // Clean up multiple spaces
            const cleanText = text.replace(/\s+/g, ' ');
            this.currentTranscript = cleanText;

            // Preserve separator elements if they exist
            const separators = this.liveTranscript.querySelectorAll('.language-swap-separator');
            const hasSeparators = separators.length > 0;

            if (hasSeparators) {
                // Keep separators, update only the text element
                let textElement = this.liveTranscript.querySelector('.live-transcript-text');
                if (!textElement) {
                    textElement = document.createElement('div');
                    textElement.className = 'live-transcript-text';
                    this.liveTranscript.insertBefore(textElement, separators[0]);
                }
                textElement.textContent = cleanText + (isPartial ? '...' : '');
                textElement.style.fontStyle = isPartial ? 'italic' : 'normal';
            } else {
                this.liveTranscript.innerHTML = this.escapeHtml(cleanText) + (isPartial ? '...' : '');
                this.liveTranscript.style.fontStyle = isPartial ? 'italic' : 'normal';
            }
        }
    }

    /**
     * Update live translation display
     */
    updateLiveTranslation(text) {
        if (this.liveTranslation) {
            // Preserve separator elements if they exist
            const separators = this.liveTranslation.querySelectorAll('.language-swap-separator');
            const hasSeparators = separators.length > 0;

            if (hasSeparators) {
                // Keep separators, update only the text element
                let textElement = this.liveTranslation.querySelector('.live-translation-text');
                if (!textElement) {
                    textElement = document.createElement('div');
                    textElement.className = 'live-translation-text';
                    this.liveTranslation.insertBefore(textElement, separators[0]);
                }
                textElement.textContent = text;
            } else {
                this.liveTranslation.innerHTML = this.escapeHtml(text);
            }
        }
    }

    /**
     * Clear live results
     */
    clearLiveResults() {
        this.liveTranscriptText = '';
        this.liveTranslationText = '';
        this.resetTTSPlayback();
        if (this.liveTranscript) {
            // Only clear text elements, keep separators
            const textElement = this.liveTranscript.querySelector('.live-transcript-text');
            if (textElement) {
                textElement.textContent = '';
                textElement.style.fontStyle = 'normal';
            }
        }
        if (this.liveTranslation) {
            // Only clear text elements, keep separators
            const textElement = this.liveTranslation.querySelector('.live-translation-text');
            if (textElement) {
                textElement.textContent = '';
            }
        }
    }

    /**
     * Close WebSocket connection
     */
    closeWebSocket() {
        if (this.ws) {
            const ws = this.ws;
            this.ws = null;

            if (ws.readyState === WebSocket.OPEN) {
                try {
                    ws.send(JSON.stringify({ type: 'end' }));
                    setTimeout(() => {
                        if (ws.readyState === WebSocket.OPEN) {
                            ws.close();
                        }
                    }, 100);
                } catch (e) {
                    ws.close();
                }
            } else {
                ws.close();
            }
        }
        this.isStreaming = false;
        this.resetTTSPlayback();
    }

    /**
     * Start sharing tab audio for live translation
     */
    async startShareTabAudio() {
        const {
            sourceLanguage: sourceLang,
            targetLanguage: targetLang
        } = this.getCurrentLanguagePair();

        if (sourceLang === targetLang) {
            this.showStatus(this.uiText('Source and target languages must be different.'), 'warning');
            return;
        }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
            this.showStatus(
                this.uiText('Tab audio sharing is not supported on mobile devices. Please use "Live Translate (Microphone)" instead, or switch to a desktop browser.'),
                'error'
            );
            this.updateUIState('ready');
            return;
        }

        this.sessionStartMode = 'tab';
        this.resetReadinessPanel(this.uiText('Opening screen sharing dialog'));
        this.updateReadinessStage('audio_permission', 'active', this.uiText('Opening screen sharing dialog'));
        this.updateUIState('connecting');
        this.clearLiveResults();
        this.visualContextSent = false;

        // First, try to get tab audio sharing - this will show the dialog
        try {
            const success = await this.audioRecorder.startTabAudio((chunk) => {
                if (this.ws && this.ws.readyState === WebSocket.OPEN && !this.isPaused) {
                    this.ws.send(chunk);
                }
            });

            if (!success) {
                throw new Error(this.uiText('Failed to start tab audio capture'));
            }
        } catch (error) {
            this.showSessionNotice(this.t('js.failed_share_tab_audio', { error: error.message }), 'error');
            this.updateReadinessStage('audio_permission', 'error', this.uiText('Tab audio unavailable'));
            this.updateUIState('ready');
            this.audioRecorder.stop();
            return;
        }

        this.updateReadinessStage('audio_permission', 'done', this.uiText('Tab audio selected'));
        this.updateReadinessStage('audio_capture', 'done', this.uiText('Tab audio captured'));

        const wsUrl = this.buildTranslationWebSocketUrl(sourceLang, targetLang, 'live');

        this.updateReadinessStage('server_connected', 'active', this.uiText('Connecting to server'));
        this.ws = new WebSocket(wsUrl);

        this.ws.onopen = () => {
            this.updateReadinessStage('server_connected', 'done', this.uiText('Server connected'));
            this.sendInitialVisualContext();
        };

        this.ws.onmessage = (event) => {
            const result = JSON.parse(event.data);
            this.handleStreamResult(result);
        };

        this.ws.onerror = (error) => {
            this.showSessionNotice(this.uiText('Connection issue detected. Please try again.'), 'error');
            this.closeWebSocket();
        };

        this.ws.onclose = () => {
            this.isStreaming = false;
            this.isPaused = false;
            this.updateUIState('ready');
        };
    }

    /**
     * Capture and send one shared-tab screenshot for session context.
     */
    async sendInitialVisualContext() {
        if (this.sessionStartMode !== 'tab' || this.visualContextSent) {
            return;
        }
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }

        this.visualContextSent = true;
        this.updateReadinessStage('visual_context', 'active', this.uiText('Capturing shared tab context'));
        const screenshot = await this.audioRecorder.captureSharedTabScreenshot();
        if (!screenshot?.imageDataUrl) {
            this.updateReadinessStage('visual_context', 'warning', this.uiText('Shared tab context unavailable'));
            return;
        }

        try {
            this.ws.send(JSON.stringify({
                type: 'visual_context',
                image_data_url: screenshot.imageDataUrl,
                width: screenshot.width,
                height: screenshot.height
            }));
        } catch (error) {
            console.warn('Failed to send visual context screenshot:', error);
            this.updateReadinessStage('visual_context', 'warning', this.uiText('Shared tab context unavailable'));
        }
    }

    /**
     * Toggle pause/resume
     */
    togglePause() {
        if (!this.isStreaming) return;

        this.isPaused = !this.isPaused;

        // Send pause/resume signal to server
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            const signal = this.isPaused ? 'pause' : 'resume';
            this.ws.send(JSON.stringify({ type: signal }));
        }

        if (this.isPaused) {
            this.showSessionNotice(this.uiText('Paused. Audio transmission stopped.'), 'info');
            if (this.pauseBtn) {
                this.pauseBtn.innerHTML = this.controlButtonHtml('control-icon-play', this.uiText('Resume'));
                this.pauseBtn.className = 'btn btn-resume';
                this.pauseBtn.setAttribute('aria-label', this.uiText('Resume live translation'));
            }
            this.updateUIState('paused');
        } else {
            this.showSessionNotice(this.uiText('Resumed. Audio transmission continues.'), 'success');
            if (this.pauseBtn) {
                this.pauseBtn.innerHTML = this.controlButtonHtml('control-icon-pause', this.uiText('Pause'));
                this.pauseBtn.className = 'btn btn-tertiary';
                this.pauseBtn.setAttribute('aria-label', this.uiText('Pause live translation'));
            }
            this.updateUIState('streaming');
        }
    }

    /**
     * Stop live translation
     */
    async stopRecording() {
        if (this.isStreaming) {
            this.closeWebSocket();
        }
        await this.audioRecorder.stop();
        this.isPaused = false;
        if (this.pauseBtn) {
            this.pauseBtn.innerHTML = this.controlButtonHtml('control-icon-pause', this.uiText('Pause'));
            this.pauseBtn.className = 'btn btn-tertiary';
            this.pauseBtn.setAttribute('aria-label', this.uiText('Pause live translation'));
        }
        this.updateUIState('ready');
        this.showSessionNotice(this.uiText('Live translation stopped.'), 'success');
    }

    /**
     * Handle tab share stopped externally (from Chrome's white strip)
     */
    handleTabShareStopped() {
        if (this.isStreaming) {
            this.closeWebSocket();
        }
        this.updateUIState('ready');
        this.showSessionNotice(this.uiText('Tab sharing stopped.'), 'success');
    }

    /**
     * Display translation result
     * @param {Object} result - API response result
     */
    displayResult(result) {
        if (result.transcript) {
            this.transcript.innerHTML = this.escapeHtml(result.transcript);
        }

        if (result.translated_text) {
            this.translation.innerHTML = this.escapeHtml(result.translated_text);
        }

        if (result.audio_url) {
            this.audioPlayer.src = result.audio_url;
            this.audioContainer.style.display = 'block';

            const autoPlay = document.querySelector('meta[name="auto-play"]')?.content === 'true';
            if (autoPlay) {
                this.audioPlayer.play().catch(() => {
                    // Auto-play prevented by browser
                });
            }
        }
    }

    /**
     * Get enabled languages from HTML dropdowns
     * Returns array of enabled language codes (excludes disabled options)
     */
    getEnabledLanguages() {
        const enabledLangs = [];
        const options = this.sourceLang ? this.sourceLang.options : [];

        for (let i = 0; i < options.length; i++) {
            if (!options[i].disabled) {
                enabledLangs.push({
                    code: options[i].value,
                    name: options[i].textContent.trim()
                });
            }
        }

        return enabledLangs;
    }

    /**
     * Load supported languages from backend API (deprecated - no longer used)
     * Kept for backward compatibility only
     */
    async loadSupportedLanguages() {
        // No longer needed - languages are hardcoded in HTML
        this.supportedLanguages = this.getEnabledLanguages();
        console.info(`Loaded ${this.supportedLanguages.length} enabled languages from HTML dropdowns`);
    }

    /**
     * Populate language dropdowns with supported languages
     * Note: Dropdowns are hardcoded in HTML - this method is no longer used
     */
    populateLanguageDropdowns() {
        // This method is deprecated - HTML has hardcoded language options
        // Kept for backward compatibility only
    }

    /**
     * Get language name from code (uses backend-sourced list)
     * @param {string} code - Language code (e.g., 'en')
     * @returns {string} - Friendly name (e.g., 'English')
     */
    getLanguageName(code) {
        if (this.supportedLanguages) {
            const lang = this.supportedLanguages.find(l => l.code === code);
            if (lang) {
                return lang.name;
            }
        }
        // Fallback to uppercase code if not found
        return code.toUpperCase();
    }

    /**
     * Update UI state based on recording state
     * States: 'ready', 'connecting', 'streaming', 'paused'
     */
    updateUIState(state) {
        switch (state) {
            case 'connecting':
                if (this.liveBtn) this.liveBtn.disabled = true;
                if (this.conversationBtn) this.conversationBtn.disabled = true;
                if (this.shareTabBtn) this.shareTabBtn.disabled = true;
                if (this.recordingControls) this.recordingControls.style.display = 'none';
                if (this.pauseBtn) this.pauseBtn.disabled = true;
                if (this.stopBtn) this.stopBtn.disabled = false;
                if (this.sessionControls) this.sessionControls.style.display = 'flex';
                this.setRecordingIndicator(this.uiText('Preparing...'), true);
                this.setLanguageFieldsDisabled(true);
                break;

            case 'streaming':
                if (this.liveBtn) this.liveBtn.disabled = true;
                if (this.conversationBtn) this.conversationBtn.disabled = true;
                if (this.shareTabBtn) this.shareTabBtn.disabled = true;
                if (this.recordingControls) this.recordingControls.style.display = 'none';
                if (this.pauseBtn) this.pauseBtn.disabled = false;
                if (this.stopBtn) this.stopBtn.disabled = false;
                if (this.sessionControls) this.sessionControls.style.display = 'flex';
                this.setRecordingIndicator(this.uiText('Listening...'), true);
                this.setLanguageFieldsDisabled(true);
                break;

            case 'paused':
                if (this.liveBtn) this.liveBtn.disabled = true;
                if (this.conversationBtn) this.conversationBtn.disabled = true;
                if (this.shareTabBtn) this.shareTabBtn.disabled = true;
                if (this.recordingControls) this.recordingControls.style.display = 'none';
                if (this.pauseBtn) this.pauseBtn.disabled = false;
                if (this.stopBtn) this.stopBtn.disabled = false;
                if (this.sessionControls) this.sessionControls.style.display = 'flex';
                this.setRecordingIndicator(this.uiText('Paused'), false);
                this.setLanguageFieldsDisabled(true);
                break;

            case 'ready':
                if (this.liveBtn) this.liveBtn.disabled = false;
                if (this.conversationBtn) this.conversationBtn.disabled = false;
                this.updateShareTabAvailability();
                if (this.recordingControls) this.recordingControls.style.display = 'flex';
                if (this.pauseBtn) this.pauseBtn.disabled = true;
                if (this.stopBtn) this.stopBtn.disabled = true;
                if (this.sessionControls) this.sessionControls.style.display = 'none';
                this.setRecordingIndicator(this.uiText('Recording'), false);
                if (!this.preserveReadinessNotice) {
                    this.setReadinessIdle();
                }
                this.setLanguageFieldsDisabled(false);
                break;

            default:
                // Fail-safe: disable language fields for unknown states
                // This prevents changing languages during unexpected states
                if (this.liveBtn) this.liveBtn.disabled = false;
                if (this.conversationBtn) this.conversationBtn.disabled = false;
                this.updateShareTabAvailability();
                if (this.recordingControls) this.recordingControls.style.display = 'flex';
                if (this.pauseBtn) this.pauseBtn.disabled = true;
                if (this.stopBtn) this.stopBtn.disabled = true;
                if (this.sessionControls) this.sessionControls.style.display = 'none';
                this.setRecordingIndicator(this.uiText('Recording'), false);
                this.setLanguageFieldsDisabled(true);
        }
    }

    /**
     * Update the recording indicator while preserving its wave markup.
     * @param {string} text - Indicator text
     * @param {boolean} visible - Whether the indicator should be visible
     */
    setRecordingIndicator(text, visible) {
        if (!this.recordingIndicator) {
            return;
        }
        this.recordingIndicator.classList.toggle('visible', visible);
        this.recordingIndicator.setAttribute('aria-label', text);
        this.recordingIndicator.innerHTML = `
            <span class="listening-wave-side listening-wave-left" aria-hidden="true">
                <span></span>
                <span></span>
                <span></span>
            </span>
            <span class="listening-mic" aria-hidden="true">
                <span class="mic-base"></span>
            </span>
            <span class="listening-wave-side listening-wave-right" aria-hidden="true">
                <span></span>
                <span></span>
                <span></span>
            </span>
        `;
    }

    /**
     * Set language fields disabled/readonly state
     * @param {boolean} disabled - Whether to disable the fields
     */
    setLanguageFieldsDisabled(disabled) {
        if (this.sourceLang) {
            this.sourceLang.disabled = disabled;
            this.sourceLang.style.opacity = disabled ? '0.5' : '1';
            this.sourceLang.style.cursor = disabled ? 'not-allowed' : 'pointer';
        }
        if (this.targetLang) {
            this.targetLang.disabled = disabled;
            this.targetLang.style.opacity = disabled ? '0.5' : '1';
            this.targetLang.style.cursor = disabled ? 'not-allowed' : 'pointer';
        }
        if (this.swapLangsBtn) {
            this.swapLangsBtn.disabled = disabled;
            this.swapLangsBtn.setAttribute('aria-disabled', disabled ? 'true' : 'false');
            this.swapLangsBtn.title = disabled
                ? this.uiText('Swap is disabled during active sessions.')
                : this.uiText('Swap Languages');
        }
    }

    /**
     * Show status message
     * @param {string} message - Status message
     * @param {string} type - Status type ('info', 'success', 'error', 'warning')
     */
    showStatus(message, type = 'info') {
        this.status.textContent = message;
        this.status.className = `status visible ${type}`;

        if (type === 'info') {
            setTimeout(() => {
                this.status.classList.remove('visible');
            }, 5000);
        }
    }

    /**
     * Show live-session notifications in the compact readiness flash.
     * @param {string} message - Notification message
     * @param {string} type - Status type ('info', 'success', 'error', 'warning')
     */
    showSessionNotice(message, type = 'info') {
        const readinessType = {
            info: 'active',
            success: 'done',
            warning: 'warning',
            error: 'error'
        }[type] || 'active';

        this.preserveReadinessNotice = true;
        this.flashReadiness(message, readinessType, true);
        this.scrollToStatusMessage();
        if (this.status) {
            this.status.classList.remove('visible');
        }
    }

    /**
     * Escape HTML to prevent XSS
     * @param {string} text - Text to escape
     * @returns {string} - Escaped text
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}
