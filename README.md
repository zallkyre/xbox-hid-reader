# xbox-hid-reader

Read raw Xbox controller input on Windows with nothing but `ctypes` and `hid.dll`.

No drivers, no `pip install`, no XInput wrapper. It enumerates HID devices through
SetupAPI, opens the controller, and decodes the raw input report into sticks,
triggers, buttons, and back paddles.

```
ls: (-0.012, 0.034)  rs: (0.0, 0.0)  lt: 0.00 rt: 0.42  p: ['p2']
```

## Why

XInput hides most of what an Elite-style controller actually sends. If you want
paddle presses, raw trigger resolution, or your own deadzone and curve, you have
to talk to the HID device directly. This is the smallest thing that does that.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer (uses `bytes | None` and `str | None` annotations)
- An Xbox controller connected over USB or the Xbox Wireless Adapter

Standard library only. The script loads `setupapi.dll`, `hid.dll`, and
`kernel32.dll` from the system.

## Run

```bash
python xbox_hid.py
```

Live view of sticks, triggers, buttons, and paddles. `Ctrl+C` to stop.

```bash
python xbox_hid.py --raw
```

Hex dump of every raw 64-byte input report. Use this to find your own byte
offsets if the defaults do not match your model.

## Supported devices

Matching is by vendor ID plus product ID:

| Vendor | Product IDs |
| ------ | ----------- |
| `0x045E` (Microsoft) | `0x02E3`, `0x0B00`, `0x0B05`, `0x02FF` |

Different models report different product IDs. To add yours, edit `PIDS` at the
top of the file. Setting `PIDS` to an empty set matches any controller from the
vendor, which is handy while you are identifying a new device.

## How the report is decoded

The parser is deliberately small, because Xbox HID reports are compact:

| Bytes | Field |
| ----- | ----- |
| `0` | Report ID (`0x01` / `0x02` are skipped as a prefix) |
| `+0..+7` | Left stick `X`/`Y`, right stick `X`/`Y` as signed 16-bit little-endian |
| `+8..+11` | Left and right trigger as unsigned 16-bit little-endian |
| `+12..+13` | Button bitfield, low byte then high byte |
| `+14` | Paddle bits: `0x01` P1, `0x02` P2, `0x04` P3, `0x08` P4 |

Stick values are divided by `32767` to land in `-1.0 .. 1.0`. Triggers are
divided by `1023` and clamped to `1.0`.

Byte offsets vary between models. If a value looks wrong, run with `--raw` and
compare against a USB descriptor dump from your device or the report
descriptor from your device.

### Output shape

`Controller.parse()` returns a plain dict, so it drops straight into whatever
consumer you have:

```python
{
    "ls": (-0.012, 0.034),
    "rs": (0.0, 0.0),
    "lt": 0.0,
    "rt": 0.42,
    "buttons": 4096,
    "paddles": {"p1": False, "p2": True, "p3": False, "p4": False},
}
```

`buttons` is the raw 16-bit bitfield. Useful bits differ per model, so it is
left undecoded rather than guessed at.

## Using it as a library

```python
from xbox_hid import Controller, find_device_path

path = find_device_path()
if path is None:
    raise SystemExit("controller not connected")

dev = Controller(path)
try:
    while True:
        raw = dev.read_raw()
        if raw:
            state = dev.parse(raw)
            if state and state["rt"] > 0.5:
                print("right trigger held")
finally:
    dev.close()
```

`read_raw()` blocks until the device sends a report. `ReadFile` is a blocking
call, so the `time.sleep` in `run()` is only a poll limiter, not a sample clock.

## Troubleshooting

**No matching controller found.** The device is not present, or its product ID is
not in `PIDS`. Open Device Manager, find the controller under Human Interface
Devices, check its hardware ID (`HID\VID_045E&PID_....`), and add the PID.

**Handle creation failed with code 5.** Something else holds the device
exclusively, usually another app reading it. Close Steam Input or any other
controller tool and retry.

**Handle creation failed with code 32.** Same cause, different wording: the device
is in use. On some setups only one reader can be attached.

**Values look scrambled.** Your model uses different offsets. `--raw` first, then
adjust `parse()`.

**Sticks drift at rest.** Real hardware does this. Apply a deadzone in your code;
the parser reports raw normalized values on purpose.

## License

MIT. See [LICENSE](LICENSE).
