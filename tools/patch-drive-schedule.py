"""Attach the persistent Drive queue to the existing n8n slot entry point."""
import os
from pathlib import Path

path = Path("clips.py")
source = path.read_text()
if "# Scheduled Telegram Drive rotation" in source:
    raise SystemExit(0)
if "# Scheduled Telegram Drive intake" in source:
    start = source.index("        # Scheduled Telegram Drive intake")
    end = source.index("        if remaining > 0:\n            used_creators =", start)
    source = source[:start] + source[end:]
    source = source.replace('        if not current and drive_status == "complete":\n            return\n', "", 1)
old = "        if remaining > 0:\n            used_creators ="
new = """        # Scheduled Telegram Drive rotation shares the existing worker lock and slot.
        drive_results = {}
        drive_worker = Path("/data/telegram-drive-worker.py")
        if remaining > 0 and drive_worker.is_file() and config.get("auto_publish"):
            publish_from = config.get("auto_publish_from")
            if not publish_from or date >= publish_from:
                import runpy
                module = runpy.run_path(str(drive_worker))
                drive_results = module["run_scheduled_slot"](sys.modules[__name__], date, slot, remaining)
                if drive_results:
                    remaining -= len(drive_results)
                    print(json.dumps({"date": date, "slot": slot, "drive_results": drive_results}))
        if remaining > 0:
            used_creators ="""
if source.count(old) != 1:
    raise SystemExit("Unexpected scheduled worker version. No changes made.")
source = source.replace(old, new)
old = "        if not current:\n            if config.get(\"instagram_only\"):"
new = """        if not current and "complete" in drive_results.values():
            return
        if not current:
            if config.get("instagram_only"):"""
if source.count(old) != 1:
    raise SystemExit("Expected reliability guard missing. No changes made.")
source = source.replace(old, new)
compile(source, str(path), "exec")
staged = path.with_suffix(".drive-schedule.tmp")
staged.write_text(source)
staged.chmod(path.stat().st_mode & 0o777)
os.replace(staged, path)
print("Scheduled Drive integration installed")
