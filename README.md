# Traductor en Tiempo Real

Traductor bidireccional de voz en tiempo real para videollamadas y cualquier aplicacion del sistema. Intercepta todo el audio del sistema para traducir conversaciones automaticamente.

## Caracteristicas

- **Traduccion bidireccional**: tu voz se traduce para ellos, su voz se traduce para ti
- **Subtitulos en tiempo real**: muestra texto original y traducido en paneles separados
- **Soporte multi-idioma**: Espanol, Ingles, Portugues, Frances, Aleman, Italiano, Japones, Chino, Coreano
- **Control total de idiomas**: configura independientemente en que idioma hablas, en que idioma te escuchan, en que idioma hablan ellos y en que idioma los escuchas
- **Intercepta todo el sistema**: funciona con WhatsApp, Zoom, Meet, Teams, YouTube o cualquier app
- **Control de audio**:
  - Mutear tu microfono
  - Silenciar la voz traducida de ellos (solo subtitulos)
  - Escuchar la voz original sin filtro (pass-through)
- **Restauracion segura**: al detener o cerrar la app, el audio del sistema vuelve a la normalidad automaticamente (incluso ante crashes)

## Requisitos

### Software
- macOS 15+ (probado en 15.7.2)
- Python 3.11+ (Homebrew, **no** el de sistema)
- [BlackHole](https://existential.audio/blackhole/) (2ch y 16ch) - dispositivos de audio virtual
- [SwitchAudioSource](https://github.com/deweller/switchaudio-osx) - control de dispositivos de audio

### APIs
- **OpenAI API Key** (obligatoria) - para transcripcion (gpt-4o-transcribe), traduccion (gpt-4o) y TTS (tts-1-hd)
- **ElevenLabs API Key** (opcional) - para clonacion de voz

## Instalacion

### 1. Instalar dependencias del sistema

```bash
# Python 3.11 con tkinter
brew install python@3.11
brew install python-tk@3.11

# Dispositivos de audio virtual
brew install --cask blackhole-2ch
brew install --cask blackhole-16ch

# Control de audio
brew install switchaudio-osx
```

> Despues de instalar BlackHole, **reinicia tu Mac**.

### 2. Instalar dependencias de Python

```bash
python3.11 -m pip install openai sounddevice numpy
```

### 3. Configurar API keys

Crea un archivo `config.json` en la raiz del proyecto:

```json
{
  "openai_api_key": "sk-...",
  "mic_device": "",
  "loopback_device": "",
  "output_device": "",
  "vb_cable_device": ""
}
```

O simplemente ejecuta la app — el wizard de configuracion te guiara.

## Uso

```bash
python3.11 main.py
```

### Flujo de uso

1. Abre la app y configura los idiomas en la barra superior
2. Presiona **Iniciar** (te pedira tu contrasena para redirigir el audio del sistema)
3. Inicia tu videollamada o reproduce un video
4. Habla normalmente — tu voz se traduce y la escuchan en el idioma configurado
5. Lo que ellos dicen aparece como subtitulos traducidos
6. Presiona **Detener** cuando termines (restaura el audio del sistema)

### Controles

| Boton | Funcion |
|---|---|
| **Iniciar** | Activa la traduccion e intercepta el audio del sistema |
| **Detener** | Detiene todo y restaura el audio normal del sistema |
| **Pausar Traduccion** | Pausa la traduccion pero sigue capturando audio |
| **Mutear Mic** | Bloquea tu microfono (no se traduce ni se envia nada) |
| **Silenciar Voz** | Alterna entre solo subtitulos y traduccion hablada de ellos |
| **Oir Original** | Activa pass-through para escuchar la voz original de ellos sin filtro |

### Selectores de idioma

- **Yo hablo en**: idioma en el que hablas
- **Ellos me escuchan en**: idioma al que se traduce tu voz
- **Ellos hablan en**: idioma en el que hablan ellos
- **Yo escucho en**: idioma al que se traduce la voz de ellos

## Arquitectura

```
Tu voz (mic real)                    Audio del sistema (BlackHole 2ch)
       |                                        |
       v                                        v
  Pipeline MINE                          Pipeline THEIRS
  (captura mic)                        (captura loopback)
       |                                        |
       v                                        v
  Whisper STT                            Whisper STT
  (transcribe)                           (transcribe)
       |                                        |
       v                                        v
  GPT-4o                                GPT-4o
  (traduce)                              (traduce)
       |                                        |
       v                                        v
  OpenAI TTS                         Subtitulos / TTS
  (genera voz)                       (segun config)
       |                                        |
       v                                        v
  BlackHole 16ch -----> Apps           Bocinas del Mac
  (sistema lo usa       lo reciben     (tu escuchas)
   como microfono)      traducido
```

### Dispositivos de audio

| Dispositivo | Funcion |
|---|---|
| **MacBook Pro (microfono)** | Captura tu voz real |
| **BlackHole 2ch** | Captura todo el audio del sistema (loopback) |
| **BlackHole 16ch** | Salida de tu voz traducida (las apps lo ven como microfono) |
| **MacBook Pro (bocinas)** | Reproduce traducciones y pass-through |

### Archivos

| Archivo | Descripcion |
|---|---|
| `main.py` | Punto de entrada, safety net para restaurar audio |
| `app.py` | Interfaz grafica (tkinter), controles, routing de audio del sistema |
| `pipeline.py` | Pipeline de traduccion: captura → STT → traduccion → TTS → playback |
| `tts_engine.py` | Motor TTS: OpenAI TTS-HD con fallback, soporte para Coqui XTTS-v2 |
| `audio_buffer.py` | Buffer de audio con deteccion de frases por silencio |
| `config.py` | Carga/guarda configuracion, auto-deteccion de dispositivos |
| `setup_wizard.py` | Wizard de configuracion inicial |

## Autor

Desarrollado por **Ronald**.
