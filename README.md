# touch-wasd

A see-through touchscreen joystick that sends **W/A/S/D** to Among Us (or any game).
Touch the bottom-left of the screen to move; tap the game's own buttons normally.

## Setup (once)

```bash
./setup.sh
```

This installs `python3-evdev` and adds a udev rule so your user can create a
virtual keyboard. If it still says "No permission", log out and back in.

## Run

1. Start Among Us. **Windowed or borderless works best**; true fullscreen may cover the overlay.
2. Click into the game once so it has keyboard focus.
3. Run:
   ```bash
   ./run.sh
   ```
4. Put a finger anywhere in the **bottom-left corner** of the screen: the joystick appears
   under it. Slide to move. Tap the game's buttons with another finger at the
   same time. Press Ctrl+C in the terminal to quit.

## Tweaks

Edit the settings block at the top of `overlay.py`: size, opacity (while moving and while resting), deadzone,
how big the bottom-left joystick zone is (`LEFT_ZONE`, `BOTTOM_ZONE`).

## Limits

- While it runs, the overlay owns the touchscreen: a bottom-left finger is the joystick,
  any other finger acts as the mouse (one at a time). Quitting gives touch back.
- Assumes the touchscreen covers your whole screen (no second monitor).
- Keys go to whatever window has focus. If you click a different app, click back into the game.
