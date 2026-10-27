/* POLYTALK TEXT TO VOICE V1 */
(() => {
    "use strict";

    function iniciar() {
        const $ = id => document.getElementById(id);

        const origen = $("tv-source-text");
        const traducido = $("tv-translated-text");
        const idiomaOrigen = $("tv-source-lang");
        const idiomaDestino = $("tv-target-lang");
        const voz = $("tv-voice-select");
        const botonTraducir = $("tv-translate-btn");
        const botonGenerar = $("tv-generate-btn");
        const estado = $("tv-status");
        const reproductor = $("tv-audio-player");
        const descarga = $("tv-download-btn");

        if (!origen || !traducido || !botonTraducir || !botonGenerar) {
            return;
        }

        function mensaje(texto) {
            estado.textContent = texto;
        }

        function limpiarAudio() {
            reproductor.pause();
            reproductor.removeAttribute("src");
            reproductor.style.display = "none";
            descarga.removeAttribute("href");
            descarga.style.display = "none";
        }

        async function solicitar(url, datos) {
            const respuesta = await fetch(url, {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(datos)
            });

            const resultado = await respuesta.json();

            if (!respuesta.ok || resultado.success === false) {
                throw new Error(
                    typeof resultado.detail === "string"
                        ? resultado.detail
                        : resultado.error || "Error del servicio"
                );
            }

            return resultado;
        }

        botonTraducir.addEventListener("click", async () => {
            const texto = origen.value.trim();

            if (!texto) {
                mensaje("Escribe un texto para traducir.");
                return;
            }

            botonTraducir.disabled = true;
            limpiarAudio();
            traducido.value = "";
            mensaje("Traduciendo texto...");

            try {
                const resultado = await solicitar("/api/text/translate", {
                    text: texto,
                    source_language: idiomaOrigen.value,
                    target_language: idiomaDestino.value
                });

                traducido.value = resultado.translated_text;
                mensaje("Traducción completada.");
            } catch (error) {
                mensaje("Error: " + error.message);
            } finally {
                botonTraducir.disabled = false;
            }
        });

        botonGenerar.addEventListener("click", async () => {
            const texto = traducido.value.trim();

            if (!texto) {
                mensaje("Primero traduce o escribe el texto de salida.");
                return;
            }

            if (texto.length > 500) {
                mensaje("La generación de voz admite hasta 500 caracteres.");
                return;
            }

            botonGenerar.disabled = true;
            limpiarAudio();
            mensaje("Generando voz con OmniVoice...");

            try {
                const resultado = await solicitar("/api/voices/synthesize", {
                    text: texto,
                    voice_id: voz.value,
                    language: idiomaDestino.value
                });

                const nombre = resultado.audio_url.split("/").pop();
                const url = "/api/audio/" + encodeURIComponent(nombre);

                reproductor.src = url;
                reproductor.style.display = "block";

                descarga.href = url;
                descarga.style.display = "inline-block";

                mensaje("Audio generado correctamente.");
            } catch (error) {
                mensaje("Error: " + error.message);
            } finally {
                botonGenerar.disabled = false;
            }
        });

        async function cargarVoces() {
            try {
                const respuesta = await fetch("/api/voices/my-list");
                if (!respuesta.ok) return;

                const datos = await respuesta.json();
                const lista = Array.isArray(datos)
                    ? datos
                    : Array.isArray(datos.voices)
                        ? datos.voices
                        : [];

                if (!lista.length) return;

                voz.replaceChildren();

                for (const item of lista) {
                    const id = item.voice_id || item.id;
                    if (!id) continue;

                    const opcion = document.createElement("option");
                    opcion.value = id;
                    opcion.textContent = item.name || item.voice_name || id;
                    voz.appendChild(opcion);
                }

                if ([...voz.options].some(
                    opcion => opcion.value === "custom_32c63483"
                )) {
                    voz.value = "custom_32c63483";
                }
            } catch (error) {
                console.warn("No fue posible cargar las voces:", error);
            }
        }

        cargarVoces();
        mensaje("Listo para traducir.");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", iniciar);
    } else {
        iniciar();
    }
})();