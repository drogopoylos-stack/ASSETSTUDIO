"""Finished phase lists leave the CLI's task folder by themselves, and the Phases panel keeps them.

The CLI repeats the whole task list in a reminder every few tool calls. One STUDIO session had
554 finished tasks in it: about 10k tokens a reminder, 1,290 reminders in its record.

Everything here runs in a temporary folder: claude_home, project_dir, DATA_DIR and settings are
replaced for the test, so no real session, task list or setting is touched.

Run: python phase_archive_test.py
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asset_studio import mission   # noqa: E402

passed = failed = 0


def check(label, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL", label, extra)


class Settings(dict):
    def get(self, k, d=None):
        return super().get(k, d)


tmp = Path(tempfile.mkdtemp(prefix="phase-archive-"))
home, data, projects = tmp / "home", tmp / "data", tmp / "projects"
SID, PID = "abcdef12-0000-0000-0000-000000000000", "test-archive-proj"
tasks = home / "tasks" / SID
tasks.mkdir(parents=True)
(projects / PID).mkdir(parents=True)
(projects / PID / (SID + ".jsonl")).write_text("", encoding="utf-8")
saved = (mission.claude_home, mission.project_dir, mission.DATA_DIR, mission.settings)
mission.claude_home = lambda project_id="": home
mission.project_dir = lambda project_id: (projects, projects / PID, PID)
mission.DATA_DIR = data
mission.settings = Settings()


def task(i, subject, status):
    (tasks / ("%d.json" % i)).write_text(json.dumps({"id": str(i), "subject": subject, "status": status,
                                                     "activeForm": "", "description": ""}), encoding="utf-8")


try:
    # Three runs, the way a session makes them and the panel groups them: a batch is created open,
    # finished later, and the next batch is made after that (rewriting a file keeps its birth time).
    task(1, "Build the thing", "pending"); task(2, "Test the thing", "pending")
    time.sleep(0.3)
    task(1, "Build the thing", "completed"); task(2, "Test the thing", "completed")
    time.sleep(1.2)
    task(3, "Ship the thing", "pending"); task(4, "Announce it", "pending")
    time.sleep(0.3)
    task(3, "Ship the thing", "completed"); task(4, "Announce it", "completed")
    time.sleep(1.2)
    task(5, "The job in hand", "in_progress")
    runs = mission._store_runs_with_files(tasks)
    check("the folder holds three runs", len(runs) == 3, [len(r["phases"]) for r in runs])

    # Nothing moves while the runs are fresh, or with the switch off.
    check("a run changed in the last half hour stays", mission.archive_finished_phase_runs(PID, SID) == 0)
    mission.settings = Settings(cc_phase_archive=False)
    check("the switch off moves nothing", mission.archive_finished_phase_runs(PID, SID, now=time.time() + 3600) == 0)
    check("...and every task is still in the folder", len(list(tasks.glob("*.json"))) == 5)
    mission.settings = Settings()

    # An hour later: the two finished runs go, the newest stays.
    moved = mission.archive_finished_phase_runs(PID, SID, now=time.time() + 3600)
    left = sorted(p.name for p in tasks.glob("*.json"))
    check("the two finished runs move out (4 tasks)", moved == 4, moved)
    check("the newest run stays in the CLI's list", left == ["5.json"], left)
    arch = list((data / "phases" / "archive" / SID[:8]).glob("*/*.json"))
    check("nothing is deleted: the four files are in the archive", sorted(p.name for p in arch) == ["1.json", "2.json", "3.json", "4.json"], arch)
    hist = json.loads(mission.phase_runs_file(PID).read_text(encoding="utf-8"))["runs"]
    check("the history file holds both finished runs", [r["sig"] for r in hist] == [["Build the thing", "Test the thing"], ["Ship the thing", "Announce it"]], hist)

    # The panel: the set on screen, and both earlier sets in its history.
    ph = mission.phase_history(PID)["runs"]
    check("history, newest first, index 0 is the set on screen", [r["phases"][0]["content"] for r in ph] == ["The job in hand", "Ship the thing", "Build the thing"], ph)
    check("an archived run is still counted done", ph[1]["done"] == ph[1]["total"] == 2, ph[1])
    cur = mission.resolve_phases(PID, [], {SID})
    check("the panel shows the job in hand", [t["content"] for t in cur["todos"]] == ["The job in hand"], cur)
    check("...and counts three sets, so the history button stays", cur["runs"] == 3, cur["runs"])

    # A second pass changes nothing, and adds no copies to the history.
    check("a second pass moves nothing", mission.archive_finished_phase_runs(PID, SID, now=time.time() + 7200) == 0)
    check("...and the history has no copies", len(json.loads(mission.phase_runs_file(PID).read_text(encoding="utf-8"))["runs"]) == 2)

    # A run with an open phase is never moved, even when it is old.
    task(6, "Left half done", "pending")
    time.sleep(1.2)
    check("the newest run is never moved, open or not", mission.archive_finished_phase_runs(PID, SID, now=time.time() + 3600) == 0)
    check("no session id: nothing to do", mission.archive_finished_phase_runs(PID, "", now=time.time() + 3600) == 0)
finally:
    mission.claude_home, mission.project_dir, mission.DATA_DIR, mission.settings = saved
    shutil.rmtree(tmp, ignore_errors=True)

print("%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
