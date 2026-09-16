"""Install catalog candidates on disposable CI sites, never on Press sites."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


WORKSPACE = Path(os.environ["GITHUB_WORKSPACE"])
BENCH = Path(os.environ["RUNNER_TEMP"]) / "frappe-bench"
OUTPUT = WORKSPACE / "catalog-results"
OUTPUT.mkdir(exist_ok=True)


def run(command, log, cwd=BENCH, timeout=900):
    log.write("\n$ " + " ".join(map(str, command)) + "\n")
    log.flush()
    subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                   check=True, timeout=timeout, env=os.environ.copy())


def validate(candidate):
    app = candidate["app"]
    site = "test_" + app
    source = Path(os.environ["RUNNER_TEMP"]) / ("source_" + app)
    result = {**candidate, "status": "running", "stage": "clone"}
    with (OUTPUT / (app + ".log")).open("w") as log:
        try:
            run(["git", "clone", "--depth", "1", "--branch", candidate["branch"],
                 "https://github.com/" + candidate["repo"] + ".git", str(source)], log)
            run(["git", "fetch", "--depth", "1", "origin", candidate["sha"]], log, cwd=source)
            run(["git", "checkout", "--detach", candidate["sha"]], log, cwd=source)
            result["stage"] = "bench-get-app"
            run(["bench", "get-app", "--skip-assets", app, str(source)], log)
            run(["bench", "pip", "check"], log)
            result["stage"] = "site-install"
            run(["bench", "new-site", site, "--db-host", "127.0.0.1", "--db-port", "3306",
                 "--db-root-username", "root", "--db-root-password", "ci_root",
                 "--mariadb-user-host-login-scope", "%", "--admin-password", "ci_admin"], log)
            run(["bench", "--site", site, "set-config", "allow_tests", "True", "--parse"], log)
            if app == "nextassist":
                run(["bench", "--site", site, "set-config", "nextassist_pg", json.dumps({"host":"127.0.0.1","port":5432,"database":"nextassist","user":"nextassist","password":"ci_pg"}), "--parse"], log)
            for dependency in ["erpnext", "payments", "hrms", app]:
                run(["bench", "--site", site, "install-app", dependency], log)
            result["stage"] = "migrate"
            run(["bench", "--site", site, "migrate"], log)
            result["stage"] = "production-build"
            run(["bench", "build", "--app", app], log)
            result["stage"] = "doctype-controller-imports"
            run(["bench", "--site", site, "execute", "frappe._catalog_smoke.run",
                 "--kwargs", json.dumps({"app": app})], log)
            result["stage"] = "upstream-regressions"
            for module in candidate.get("test_modules", []):
                run(["bench", "--site", site, "run-tests", "--module", module], log)
            result["stage"] = "focused-regressions"
            run(["bench", "--site", site, "execute", app + ".tests.v16_smoke.run", "--kwargs", json.dumps({"app": app})], log)
            run(["bench", "--site", site, "list-apps"], log)
            result.update(status="passed", stage="complete")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            result.update(status="failed", error=str(error))
        finally:
            (BENCH / "sites/apps.txt").write_text("frappe\nerpnext\nhrms\npayments\n")
            subprocess.run(["uv", "pip", "uninstall", "--python", str(BENCH / "env/bin/python"), app],
                           stdout=log, stderr=subprocess.STDOUT)
            shutil.rmtree(BENCH / "apps" / app, ignore_errors=True)
            shutil.rmtree(source, ignore_errors=True)
    (OUTPUT / (app + ".json")).write_text(json.dumps(result, indent=2))
    print(json.dumps({key: result[key] for key in ("app", "status", "stage")}), flush=True)
    return result


def main():
    shard = int(sys.argv[1])
    candidates = json.loads((WORKSPACE / ".github/catalog/candidates.json").read_text())
    candidates = [row for row in candidates if not row.get("deferred")]
    results = [validate(row) for index, row in enumerate(candidates) if index % 4 == shard]
    (OUTPUT / f"shard-{shard}.json").write_text(json.dumps(results, indent=2))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write("| App | Result | Stage |\n|---|---|---|\n")
        for row in results:
            summary.write(f"| {row['app']} | {row['status']} | {row['stage']} |\n")
    return int(any(row["status"] != "passed" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
