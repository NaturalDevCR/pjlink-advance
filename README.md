# PJLink Advance

Integración `pjlink` para Home Assistant que **reemplaza** a la de core y usa
todo el protocolo PJLink Class 1 y Class 2. Incluye la librería asíncrona
[`pjlink-advance`](lib/) con un emulador de proyector para probar sin hardware.

*English summary below.*

## Qué agrega frente a la integración de core

| Función | Core | PJLink Advance |
|---|---|---|
| Encendido, entradas, mute de audio | ✅ | ✅ |
| Estados intermedios (calentando / enfriando) | ❌ | ✅ sensor de estado de energía |
| Nombres reales de entradas (Class 2) | ❌ | ✅ "HDMI1" en vez de "DIGITAL 1" |
| Video mute (pantalla en negro) | ❌ | ✅ switch |
| Congelar imagen (Class 2) | ❌ | ✅ switch |
| Volumen +/− del parlante (Class 2) | ❌ | ✅ en el media player |
| Volumen del micrófono (Class 2) | ❌ | ✅ botones (deshabilitados por defecto) |
| Horas de lámpara / fuente de luz | ❌ | ✅ un sensor por lámpara |
| Horas de filtro (Class 2) | ❌ | ✅ |
| Alarmas por categoría (ventilador, lámpara, temperatura, tapa, filtro, otros) | ❌ | ✅ sensores + binary_sensor "Problema" |
| Resolución de entrada y recomendada (Class 2) | ❌ | ✅ |
| Repuestos de lámpara y filtro (Class 2) | ❌ | ✅ |
| Ficha del dispositivo (fabricante, modelo, serie, firmware, MAC) | ❌ | ✅ |
| Búsqueda en la red (Class 2) | ❌ | ✅ en el config flow |
| Notificaciones push por UDP (Class 2) | ❌ | ✅ opcional |
| Reauth, reconfigure, opciones | ❌ | ✅ |
| Avisos en Repairs (lámpara, filtro, errores) | ❌ | ✅ |
| Acción `pjlink.send_command` con respuesta | ❌ | ✅ |
| Diagnóstico descargable | ❌ | ✅ |
| Conexión persistente con reconexión automática | ❌ (una conexión por consulta) | ✅ |
| Sondeo adaptativo (5 s durante calentamiento) | ❌ | ✅ |
| Traducciones | EN | EN + ES |

Las entidades de Class 2 solo se crean si el proyector es Class 2 y responde al
comando (si contesta `ERR1`, la entidad no aparece).

## Compatibilidad con la integración de core

- Usa el mismo dominio `pjlink`; HA carga esta versión en lugar de la de core.
- Las entradas existentes (versión 1) se migran solas a la versión 2.
- El `media_player` conserva el mismo `unique_id`, así que sus `entity_id`
  y automatizaciones siguen funcionando.
- La configuración YAML (`media_player: - platform: pjlink`) se sigue
  importando como en core.

## Instalación

> **Requisito:** Home Assistant instala `pjlink-advance` desde PyPI. Hay que
> publicar la librería antes de instalar la integración (ver
> [Publicar la librería](#publicar-la-librería)).

### HACS (repositorio personalizado)

1. HACS → Integraciones → ⋮ → Repositorios personalizados →
   `https://github.com/NaturalDevCR/pjlink-advance`, categoría *Integration*.
2. Instala **PJLink Advance** y reinicia Home Assistant.
3. Ajustes → Dispositivos y servicios → Añadir integración → **PJLink**.

### Manual

Copia `custom_components/pjlink` a `config/custom_components/pjlink` y reinicia.

## Opciones

| Opción | Por defecto | Descripción |
|---|---|---|
| Intervalo de sondeo | 30 s | En reposo. Calentamiento/enfriamiento siempre cada 5 s. |
| Aviso de lámpara | 20000 h | Crea un aviso en Repairs. 0 lo desactiva. |
| Aviso de filtro | 0 (desactivado) | Igual que el anterior. |
| Notificaciones Class 2 | desactivado | Escucha en UDP 4352. Con push activo el sondeo baja a cada 5 min. |

Las notificaciones push y la búsqueda necesitan que HA y el proyector estén en
la misma subred y que HA reciba UDP en el puerto 4352 (HA OS, o Docker con
`network_mode: host`). Si el puerto está ocupado, la integración sigue
funcionando solo con sondeo.

## Notas para Epson EB-L735U

- Activa la comunicación en espera (opción tipo *Standby Mode:
  Communication On* en el menú de red). Si no, el proyector no responde cuando
  está apagado y aparece como no disponible.
- Es láser: "Horas de lámpara" son las horas de la fuente de luz y no hay
  modelo de lámpara de repuesto.
- Durante el calentamiento responde `ERR3` a varios comandos; la integración
  conserva los últimos valores mientras tanto.
- Funciones propias de Epson (modo de imagen, lente) usan ESC/VP.net y están
  fuera de alcance; puedes probarlas con `pjlink.send_command` solo si el
  proyector las expone por PJLink.

## Ejemplo: `pjlink.send_command`

```yaml
action: pjlink.send_command
data:
  config_entry_id: 0123456789abcdef
  command: SVER
  pj_class: 2
response_variable: respuesta
```

## Desarrollo

```bash
uv sync --python 3.14            # entorno de la integración (HA + tests)
uv run pytest -q                 # tests de la integración
cd lib && uv run --extra test pytest -q   # tests de la librería
```

Emulador de proyector (Class 2, TCP y UDP en 4352):

```bash
cd lib && uv run python -m pjlink_advance.emulator --port 4352 --password secreto
```

## Publicar la librería

```bash
cd lib && uv build && uv publish
```

Después crea un tag `v0.1.0` del repositorio para HACS. Si cambias la versión
de la librería, actualiza `requirements` en
`custom_components/pjlink/manifest.json`.

## Hacia upstream (HA core)

La librería ya está separada como exige core. Un PR futuro debería ir por
partes: primero cambiar `pypjlink2` por `pjlink-advance` con paridad de
funciones, luego plataformas nuevas (sensores, switches, etc.).

---

## English summary

`pjlink` custom integration that replaces the Home Assistant core one. It adds
Class 2 support (input names, freeze, volume, filter hours, resolutions,
replacement parts, network search, UDP status notifications), lamp hours,
per-category error sensors, video mute, device info, reauth/reconfigure/options
flows, repair issues, diagnostics and a `pjlink.send_command` action. Existing
core entries migrate automatically and the media player keeps its unique id.
Protocol code lives in the separate `pjlink-advance` library (with a projector
emulator), which must be published to PyPI before installing the integration.
