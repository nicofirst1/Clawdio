# Theme packs

A theme pack is one JSON file that overrides a whitelisted set of `AmbientConfig` fields. It changes the sound of the `ambient` theme without touching code. The `geiger` theme is legacy and does not use packs.

## Where packs live

Two search directories, scanned in this order:

1. `<repo>/themes/*.json` (ships with the repo; `dusk.json` and `porcelain.json` today)
2. `~/.config/claudio/themes/*.json` (yours)

The file's basename (minus `.json`) is the pack name. If the same name exists in both directories, the user directory wins.

## Selecting a pack

Any one of:

- `CLAUDIO_THEME_PACK=dusk`
- `theme_pack: "dusk"` in the config file
- the web panel's theme-pack dropdown

Default is `""`, the built-in sound, unchanged. A pack is a **restart key**: picking one arms the pending-restart banner in the panel, or re-run the daemon (`POST /restart` or a plain relaunch) to hear it.

## Graceful degradation

A pack can never crash the daemon or break the sound:

- Unknown keys are dropped, with a warning in the log.
- Out-of-range numeric values are clamped to the field's bounds, with a warning.
- A missing pack name, corrupt JSON, oversized file (>64 KB), or non-object JSON body all fall back to `{}`, the built-in sound, with a warning.
- `name`, `author`, `description`, `version`, and any key starting with `_` are metadata for humans. The loader ignores them silently, no warning.

## Example

```json
{
  "name": "dusk",
  "description": "Darker and slower.",
  "drop_timbre": "marimba",
  "BED_LP_BASE_HZ": 850,
  "DROP_RATE_SCALE": 0.35
}
```

## Field reference

Every key a pack may set, its default, its bounds or allowed set, and what it does. Values outside the bounds get clamped, not rejected.

| Field                      | Default     | Bounds / set                         | Meaning                                         |
| -------------------------- | ----------- | ------------------------------------ | ----------------------------------------------- |
| `drop_timbre`              | `woodblock` | `{woodblock, marimba, plink, noise}` | grain timbre for melodic drop events            |
| `done_cadence`             | `v24`       | `{v22, v24}`                         | Stop-event cadence style                        |
| `DROP_RATE_SCALE`          | `0.5`       | 0.1 - 1.5                            | multiplier on drop event rate                   |
| `DROP_MIN_GAP_S`           | `0.3`       | 0.05 - 1.0                           | minimum seconds between drops                   |
| `BURST_COALESCE_WINDOW_S`  | `0.5`       | 0.1 - 2.0                            | window for coalescing rapid-fire drops into one |
| `DROP_CAL_DB`              | `2.0`       | -10 - 8                              | drop layer level trim                           |
| `DROP_AMP_SPREAD_DB`       | `4.0`       | 0 - 8                                | random amplitude spread per drop                |
| `BED_LP_BASE_HZ`           | `1150`      | 300 - 2400                           | low pad lowpass corner, resting value           |
| `BED_LP_MAX_HZ`            | `3200`      | 600 - 6000                           | low pad lowpass corner, ceiling under activity  |
| `BED_CAL_DB`               | `19.0`      | 13 - 25                              | low pad layer level trim                        |
| `MIDLAYER_CAL_DB`          | `21.0`      | 15 - 27                              | mid layer level trim                            |
| `MIDLAYER_LP_HZ`           | `2200`      | 500 - 6000                           | mid layer lowpass corner                        |
| `AIR_CAL_DB`               | `19.0`      | 13 - 25                              | air bed layer level trim                        |
| `AIR_TILT_HI_IDLE_HZ`      | `2600`      | 800 - 6000                           | air bed high-shelf corner at idle               |
| `AIR_TILT_HI_ACTIVE_HZ`    | `4000`      | 1000 - 8000                          | air bed high-shelf corner at full activity      |
| `AIR_V23_HARD_CEILING_HZ`  | `2800`      | 1000 - 8000                          | hard lowpass ceiling on the air bed             |
| `AIR_V23_LEVEL_CUT_DB`     | `-4.0`      | -12 - 0                              | extra trim on the air bed                       |
| `AIR_FLOOR_OFFSET_DB`      | `-1.0`      | -8 - 3                               | air bed level floor offset                      |
| `AIR_ACTIVITY_RANGE_DB`    | `5.0`       | 0 - 10                               | air bed level swing from idle to active         |
| `AIR_GLOOM_DEPTH`          | `0.5`       | 0 - 1                                | how much a failure dims the air bed             |
| `NOTE_EMBED_CAP_DB`        | `10.0`      | 4 - 16                               | max reverb-embed level for melodic notes        |
| `NOTE_EMBED_CAP_IDLE_DB`   | `6.0`       | 2 - 12                               | max reverb-embed level for notes at idle        |
| `KNOCK_EMBED_CAP_DB`       | `16.0`      | 10 - 22                              | max reverb-embed level for the failure knock    |
| `DUCK_DEPTH_DB`            | `3.0`       | 0 - 6                                | how much the bed ducks under a foreground event |
| `NOTE_DIRECT_FRAC`         | `0.8`       | 0 - 1.2                              | direct (dry) fraction of a melodic note         |
| `NOTE_REVERB_FRAC`         | `0.15`      | 0 - 1.0                              | reverb-send fraction of a melodic note          |
| `AMBIENT_WET_GAIN`         | `0.426`     | 0 - 1.0                              | reverb wet bus gain                             |
| `AMBIENT_DRY_GAIN`         | `1.3`       | 0.5 - 2.0                            | dry bus gain                                    |
| `STEM_CAL_DB`              | `18.0`      | 12 - 24                              | subagent stem layer level trim                  |
| `STEM_LP_HZ`               | `520`       | 200 - 4000                           | subagent stem lowpass corner                    |
| `SUBBASS_CAL_DB`           | `18.0`      | 12 - 24                              | sub-bass layer level trim                       |
| `WHOOSH_CAL_DB`            | `30.0`      | 24 - 36                              | context-pressure whoosh level trim              |
| `SETTLED_BED_DB`           | `-38.0`     | -50 - -28                            | bed level once settled after Stop               |
| `SETTLED_BED_TAU_S`        | `8.0`       | 2 - 20                               | time constant easing into the settled bed level |
| `SETTLED_BLOOM_RATE_SCALE` | `0.35`      | 0 - 1                                | bloom rate multiplier once settled              |

Exact values and bounds live in `PACK_SCHEMA` (`src/themes.py`); defaults live in `AmbientConfig` (`src/ambient_layers.py`).

## Share your pack

Render a deterministic clip and attach it, or the pack JSON alone, to the [theme pack issue template](../../../issues/new?template=theme-pack.yml):

```bash
CLAUDIO_THEME_PACK=yourpack python3 src/main.py --render demos/demo-session-v2.jsonl clip.wav --seed 7
```
