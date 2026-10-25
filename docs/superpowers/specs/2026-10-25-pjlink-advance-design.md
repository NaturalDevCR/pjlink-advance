# PJLink Advance — Diseño

Fecha: 2026-10-25
Estado: borrador para revisión

## 1. Objetivo

Reemplazar la integración `pjlink` de Home Assistant core con un custom component
(mismo dominio `pjlink`) que aproveche el protocolo PJLink Class 1 y Class 2
completo, sea compatible con las config entries existentes y quede preparado
para un PR upstream posterior.

Hardware de referencia para pruebas reales: **Epson EB-L735U** (láser, Class 2).

## 2. Situación actual (HA core, rama `dev`)

- Una sola entidad `media_player`: encendido/apagado, mute de audio, selección
  de entrada.
- Librería `pypjlink2` síncrona; abre una conexión TCP nueva por cada consulta y
  comando, sin coordinator.
- `warm-up` se reporta como `on` y `cooling` como `off`; no hay estados
  intermedios.
- Errores detectados comparando strings (`"projector unavailable"`).
- `CONF_ENCODING` definido pero sin uso.
- Sin device info (fabricante, modelo, serie, firmware).
- Config flow `VERSION = 1`, `data = {host, port, password?}`, título = nombre
  del proyector, `unique_id` de entidad = `entry.entry_id`. Sin `unique_id` de
  config entry. Sin reauth, reconfigure ni options.
- `codeowners: []`.

## 3. Decisiones

| Tema | Decisión | Motivo |
|---|---|---|
| Dominio | `pjlink` | Sobrescribe core; el upstream es una mejora de la integración existente, no una duplicada. |
| Protocolo | Librería propia, async, publicada en PyPI aparte | HA core exige el protocolo fuera de la integración. |
| ¿Por qué no `aiopjlink`? | Existe (MIT, Class 1+2) pero su roadmap deja pendientes Search (§3.2) y Status Notification (§3.3). | Necesitamos push, discovery, reconexión transparente y emulador bajo nuestro control. Su código sirve de referencia. |
| Nombre del paquete | `pjlink-advance` (import `pjlink_advance`); libre en PyPI al 2026-10-25 | `aiopjlink` y `aiopjlink2` ya están tomados. |
| Modelo de actualización | Polling (base) + push UDP Class 2 (opcional) | Push depende de la red (misma subred, host network). |
| `iot_class` | `local_polling` | El polling siempre funciona; el push es una mejora. |

## 4. Estructura del repositorio

```
PJLink Advance/
├── lib/                         # paquete PyPI pjlink-advance
│   ├── pyproject.toml
│   ├── src/pjlink_advance/
│   │   ├── __init__.py
│   │   ├── protocol.py          # encode/decode puro, sin I/O
│   │   ├── client.py            # PJLinkClient (TCP async)
│   │   ├── listener.py          # UDP: search + notificaciones
│   │   ├── models.py            # dataclasses y enums
│   │   ├── errors.py
│   │   └── emulator.py          # proyector falso TCP+UDP para tests
│   └── tests/
├── custom_components/pjlink/    # integración HA
├── tests/                       # tests de la integración
├── hacs.json
└── docs/
```

Durante el desarrollo la integración instala la librería desde el repo; al
publicar se fija `requirements: ["pjlink-advance==X.Y.Z"]`.

## 5. Librería `pjlink_advance`

Sin dependencias de Home Assistant. Python ≥ 3.13, tipada, `asyncio` puro.

### 5.1 `protocol.py` (puro)

- Construye peticiones: `%1POWR ?\r`, `%2INPT 31\r`, etc.
- Parsea respuestas: `%1POWR=1`, `ERR1..ERR4`, `ERRA`.
- Autenticación: saludo `PJLINK 0` (sin clave) o `PJLINK 1 <random>`;
  digest = `md5(random + password)` antepuesto al primer comando.
- Decodifica con el `encoding` configurado (por defecto `utf-8`, fallback
  `latin-1` para NAME/INF*).

### 5.2 `client.py` — `PJLinkClient`

- `PJLinkClient(host, port=4352, password=None, encoding="utf-8", timeout=5.0)`.
- Una conexión TCP persistente protegida por `asyncio.Lock`; los comandos se
  serializan. El proyector cierra la sesión tras ~30 s de inactividad: el
  cliente reconecta y reautentica de forma transparente (un reintento).
- API de alto nivel:

| Clase | Método | Comando |
|---|---|---|
| 1 | `get_power()` / `set_power(bool)` | POWR |
| 1 | `get_input()` / `set_input(InputSource)` / `get_inputs()` | INPT / INST |
| 1 | `get_av_mute()` / `set_av_mute(MuteTarget, bool)` | AVMT |
| 1 | `get_errors()` | ERST |
| 1 | `get_lamps()` | LAMP |
| 1 | `get_name()`, `get_manufacturer()`, `get_model()`, `get_other_info()`, `get_class()` | NAME, INF1, INF2, INFO, CLSS |
| 2 | `get_serial()`, `get_software_version()` | SNUM, SVER |
| 2 | `get_input_name(InputSource)` | INNM |
| 2 | `get_input_resolution()`, `get_recommended_resolution()` | IRES, RRES |
| 2 | `get_filter_hours()` | FILT |
| 2 | `get_lamp_model()`, `get_filter_model()` | RLMP, RFIL |
| 2 | `speaker_volume(up: bool)`, `mic_volume(up: bool)` | SVOL, MVOL |
| 2 | `get_freeze()` / `set_freeze(bool)` | FREZ |
| — | `send_raw(command, param, pj_class)` | cualquiera |

- `async fetch_static()` → `DeviceInfo` (name, manufacturer, model, info, class,
  serial, software, inputs con nombres).
- `async fetch_state(full: bool)` → `ProjectorState` (power, input, mute,
  freeze, resoluciones; con `full=True` también errores, lámparas y filtro).

### 5.3 Modelos (`models.py`)

- `PowerState`: `OFF`, `ON`, `COOLING`, `WARMING`.
- `InputSource(type: InputType, index: str)` con `InputType` RGB=1, VIDEO=2,
  DIGITAL=3, STORAGE=4, NETWORK=5, INTERNAL=6 (Class 2); índice `1-9`
  (Class 1) o `1-9A-Z` (Class 2).
- `MuteState(video: bool, audio: bool)`; `MuteTarget`: `VIDEO`=1, `AUDIO`=2,
  `BOTH`=3 (AVMT `11/10`, `21/20`, `31/30`).
- `ErrorStatus`: `fan`, `lamp`, `temperature`, `cover`, `filter`, `other`, cada
  uno `OK | WARNING | ERROR`.
- `Lamp(hours: int, on: bool)`; lista de 1 a 8.
- `Resolution(width, height)` | `NO_SIGNAL` | `UNKNOWN`.

### 5.4 Errores (`errors.py`)

`PJLinkError` base y subclases: `PJLinkConnectionError`, `PJLinkAuthError`
(`ERRA`), `PJLinkUndefinedCommand` (`ERR1`), `PJLinkOutOfParameter` (`ERR2`),
`PJLinkUnavailableTime` (`ERR3`), `PJLinkProjectorFailure` (`ERR4`),
`PJLinkProtocolError` (respuesta mal formada).

### 5.5 `listener.py` — UDP Class 2

- `PJLinkListener`: un socket UDP en el puerto 4352 compartido por todos los
  proyectores.
- `async discover(timeout=5)` → envía `%2SRCH` por broadcast y devuelve
  `[(ip, mac)]` a partir de `%2ACKN=<mac>`.
- Notificaciones: `%2LKUP`, `%2ERST`, `%2POWR`, `%2INPT`, `%2AVMT`. Las
  entrega a callbacks registrados por IP de origen.

### 5.6 `emulator.py`

Proyector falso (TCP + UDP) con estado configurable, clase 1 o 2, clave
opcional, inyección de errores (`ERR3` durante el calentamiento, desconexiones,
respuestas lentas) y envío de notificaciones. Lo usan los tests de la librería y
de la integración, y sirve para desarrollar sin hardware.

## 6. Integración `custom_components/pjlink`

### 6.1 Config flow

- **user**: host, port (4352), password opcional. Valida conectando y leyendo
  la información estática.
- **Búsqueda en red** (Class 2): primer paso del menú "Buscar proyectores" que
  usa `discover()` y lista los encontrados; "Manual" como alternativa.
- `unique_id` de la config entry: número de serie (Class 2) o MAC si se conoce;
  si no hay ninguno, `host:port`.
- **reauth**: ante `PJLinkAuthError` en tiempo de ejecución.
- **reconfigure**: cambiar host, puerto o clave sin borrar la entry.
- **options**: intervalo de sondeo (por defecto 30 s), umbral de horas de lámpara
  y de filtro para Repairs, activar push.

### 6.2 Migración desde core

- La config flow sube a `VERSION = 2`.
- `async_migrate_entry` (1 → 2): conserva `data` y título, añade
  `encoding = "utf-8"` y fija el `unique_id` de la entry en el primer setup
  exitoso.
- El `media_player` mantiene `unique_id = entry.entry_id` para no romper
  automatizaciones ni `entity_id`. Las entidades nuevas usan
  `f"{entry.entry_id}_{key}"`.
- La importación desde YAML (`async_step_import`) se mantiene mientras core la
  tenga (`breaks_in_ha_version 2026.11.0`).

### 6.3 Coordinator

- `PJLinkCoordinator(DataUpdateCoordinator[ProjectorState])`, un cliente por
  entry.
- La información estática se lee en el setup y al pasar de apagado a encendido
  (las entradas pueden cambiar).
- Sondeo adaptativo: 5 s durante `WARMING`/`COOLING`; el intervalo configurado
  en el resto de casos. Errores, lámparas y filtro se leen cada 5 min o cuando
  llega una notificación `ERST`.
- `PJLinkUnavailableTime` (`ERR3`) no es un fallo: se conservan los últimos
  valores y la entidad sigue disponible.
- `PJLinkConnectionError` → `UpdateFailed` (entidades no disponibles).
- `PJLinkAuthError` → `ConfigEntryAuthFailed` (dispara reauth).
- `PJLinkUndefinedCommand` en un comando opcional → se marca como no soportado
  y su entidad no se crea (o se deja no disponible si ya existe).
- Con push activo, cada notificación actualiza el estado con
  `async_set_updated_data` y el sondeo pasa a 5 min como respaldo.

### 6.4 Entidades

Todas con `has_entity_name = True`, `translation_key` e iconos en
`icons.json`. Las de Class 2 solo se crean si `CLSS = 2` y el comando responde.

| Plataforma | Entidad | Clase | Notas |
|---|---|---|---|
| media_player | Proyector | 1 | on/off, fuente (con nombres INNM si existen), mute de audio; `volume_up`/`volume_down` con Class 2 |
| sensor | Estado de energía | 1 | enum off/warming/on/cooling |
| sensor | Horas de lámpara N | 1 | `UnitOfTime.HOURS`, `TOTAL_INCREASING`; una por lámpara (en el Epson láser es la fuente de luz) |
| sensor | Horas de filtro | 2 | `UnitOfTime.HOURS`, `TOTAL_INCREASING` |
| sensor | Ventilador / Lámpara / Temperatura / Tapa / Filtro / Otros | 1 | enum ok/warning/error |
| sensor | Resolución de entrada | 2 | texto `1920x1200`, `no_signal` |
| sensor | Resolución recomendada | 2 | diagnóstico, deshabilitada por defecto |
| sensor | Modelo de lámpara / filtro de repuesto | 2 | diagnóstico |
| binary_sensor | Problema | 1 | `PROBLEM`, `on` si alguna categoría ERST no está en ok |
| switch | Video mute (pantalla en negro) | 1 | AVMT 11/10 |
| switch | Congelar imagen | 2 | FREZ |
| button | Micrófono volumen +/− | 2 | deshabilitados por defecto |

Device info: fabricante (INF1), modelo (INF2), `serial_number` (SNUM),
`sw_version` (SVER), nombre (NAME), conexión MAC si se conoce.

### 6.5 Acciones

- `pjlink.send_command`: `command`, `parameter`, `pj_class` (1/2). Devuelve la
  respuesta (`SupportsResponse.OPTIONAL`). Para funciones del fabricante no
  cubiertas.

### 6.6 Repairs

- Horas de lámpara ≥ umbral → aviso con el modelo de repuesto si hay RLMP.
- Horas de filtro ≥ umbral → aviso con RFIL.
- `ERR4` o una categoría ERST en `ERROR` → aviso hasta que se resuelva.
- Los avisos se borran solos cuando la condición desaparece.

### 6.7 Diagnóstico y traducciones

- `diagnostics.py`: información estática, último estado y opciones; la clave
  queda redactada.
- `strings.json` + `translations/en.json` + `translations/es.json`.
- Comandos fallidos → `HomeAssistantError` con `translation_key`.

## 7. Notas del Epson EB-L735U (a verificar en hardware)

- Para responder en standby necesita activar la comunicación en espera
  (opción tipo "Standby Mode: Communication On"; el nombre exacto depende del
  menú/firmware). Si no, se ve no disponible cuando está apagado.
- Es láser: `LAMP` devuelve las horas de la fuente de luz y no aplica
  reemplazo de lámpara (RLMP vacío o `ERR1`).
- Responde `ERR3` en varios comandos durante el calentamiento.
- Comprobar qué comandos Class 2 soporta: INNM, IRES, FREZ, FILT, SVOL, y si
  envía notificaciones.
- Epson también tiene ESC/VP.net (TCP 3629) con funciones extra (modo de
  imagen, lente). **Fuera de alcance** en esta versión; posible integración
  aparte.

## 8. Pruebas

- Librería: `pytest` + `pytest-asyncio`. Tests unitarios del parser con
  vectores de la especificación y tests de integración contra el emulador:
  auth, reconexión, timeouts, ERR1–ERR4, push y discovery.
- Integración: `pytest-homeassistant-custom-component` con el emulador como
  fixture: config flow (user, búsqueda, reauth, reconfigure, options),
  migración v1→v2 con una entry real de core, entidades por clase, Repairs y
  diagnóstico.
- Manual: checklist en el Epson L735U, incluido push con HA en host network.
- CI: GitHub Actions con ruff, mypy, pytest y validación de HACS y hassfest.

## 9. Fases

1. **Librería + emulador**: protocol, client, models, errors, emulator y tests
   de Class 1 y 2 (sin UDP).
2. **Integración base**: config flow, migración, coordinator, media_player,
   sensores, switch de video mute, device info, diagnóstico y traducciones.
3. **Extras Class 2**: nombres de entrada, freeze, volumen, resoluciones,
   repuestos, botones de micrófono, acción `send_command` y Repairs.
4. **UDP**: discovery en el config flow y notificaciones push.
5. **Distribución**: HACS, README, CI, publicación en PyPI y preparación del PR
   upstream (por partes: primero la librería + paridad, luego plataformas).

## 10. Fuera de alcance

- ESC/VP.net u otros protocolos propietarios.
- Control de lente y obturador (no forman parte de PJLink).
- Escenas o tarjetas Lovelace personalizadas.
