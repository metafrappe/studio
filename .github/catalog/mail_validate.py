"""Validate Mail and Suite in a disposable CI bench with real MariaDB sites."""

import json
import os
from pathlib import Path
import subprocess
import sys


WORKSPACE = Path(os.environ["GITHUB_WORKSPACE"])
TEMPORARY = Path(os.environ["RUNNER_TEMP"])
BENCH = TEMPORARY / "frappe-bench"
OUTPUT = WORKSPACE / "mail-results"
PINS = json.loads((WORKSPACE / ".github/catalog/mail-pins.json").read_text())
SITES = {"mail": "test_mail", "suite": "test_suite"}


def run(command, log, cwd=BENCH, timeout=900):
    log.write("\n$ " + " ".join(map(str, command)) + "\n")
    log.flush()
    subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                   check=True, timeout=timeout, env=os.environ.copy())


def fetch_sources(log):
    for app, candidate in PINS.items():
        source = TEMPORARY / ("source_" + app)
        run(["git", "clone", "--depth", "1", "--branch", candidate["branch"],
             "https://github.com/" + candidate["repo"] + ".git", str(source)], log, WORKSPACE)
        run(["git", "fetch", "--depth", "1", "origin", candidate["sha"]], log, source)
        run(["git", "checkout", "--detach", candidate["sha"]], log, source)
        if app == "mail":
            run(["git", "submodule", "update", "--init", "--recursive"], log, source)
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        assert actual == candidate["sha"], (app, actual, candidate["sha"])
    (OUTPUT / "source-pins.json").write_text(json.dumps(PINS, indent=2) + "\n")


def prepare_apps(log):
    for app in PINS:
        run(["bench", "get-app", "--skip-assets", app, str(TEMPORARY / ("source_" + app))], log)
    run(["git", "submodule", "update", "--init", "--recursive"], log, BENCH / "apps/mail")
    run(["bench", "pip", "check"], log)
    run(["bench", "version"], log)


def install_site(app, log):
    site = SITES[app]
    run(["bench", "new-site", site, "--db-host", "127.0.0.1", "--db-port", "3306",
         "--db-root-username", "root", "--db-root-password", "ci_root",
         "--mariadb-user-host-login-scope", "%", "--admin-password", "ci_admin"], log)
    for key in ("allow_tests", "mute_emails", "pause_scheduler"):
        run(["bench", "--site", site, "set-config", key, "True", "--parse"], log)
    for dependency in ("erpnext", "payments", "hrms", app):
        run(["bench", "--site", site, "install-app", dependency], log)


def migrate_sites(log):
    for _ in range(2):
        for site in SITES.values():
            run(["bench", "--site", site, "migrate"], log)


def build_apps(log):
    for app in PINS:
        run(["bench", "build", "--app", app], log, timeout=1800)


def check_controllers(log):
    for app, site in SITES.items():
        run(["bench", "--site", site, "execute", "frappe._catalog_smoke.run",
             "--kwargs", json.dumps({"app": app})], log)


def check_workflows(log):
    for app, site in SITES.items():
        run(["bench", "--site", site, "execute", app + ".tests.v16_smoke.run",
             "--kwargs", json.dumps({"app": app})], log)


def expect_install_rejection(site, app, marker, log):
    command = ["bench", "--site", site, "install-app", app]
    log.write("\n$ " + " ".join(command) + " (expected rejection)\n")
    result = subprocess.run(command, cwd=BENCH, capture_output=True, text=True, timeout=300)
    output = result.stdout + result.stderr
    log.write(output)
    log.flush()
    assert result.returncode != 0 and marker in output, (site, app, result.returncode, output[-3000:])


def check_install_guards(log):
    # The pinned Frappe installer invokes the target app's before_install first.
    expect_install_rejection(SITES["mail"], "suite", "Cannot install Frappe Suite", log)
    expect_install_rejection(SITES["suite"], "mail",
                             "Frappe Mail and Frappe Suite must be installed on separate sites.", log)
    check_workflows(log)


def check_namespaces(log):
    run([str(BENCH / "env/bin/python"), "-c",
         "from mail.tests.v16_smoke import run_namespace_isolation; "
         "run_namespace_isolation(sites=('test_mail', 'test_suite'))"], log, cwd=BENCH / "sites")


def record_sites(log):
    for app, site in SITES.items():
        code = (
            "import frappe, sys; frappe.init(sys.argv[1]); frappe.connect(); "
            "installed = set(frappe.get_installed_apps()); "
            "assert {'frappe', 'erpnext', 'payments', 'hrms', sys.argv[2]} <= installed, installed; "
            "assert ({'mail', 'suite'} & installed) == {sys.argv[2]}, installed; "
            "frappe.destroy()"
        )
        run([str(BENCH / "env/bin/python"), "-c", code, site, app], log, cwd=BENCH / "sites")
        result = subprocess.run(["bench", "--site", site, "list-apps", "--format", "json"],
                                cwd=BENCH, capture_output=True, text=True, check=True, timeout=60)
        (OUTPUT / (site + "-apps.json")).write_text(result.stdout)
        log.write(result.stdout)
    run(["bench", "pip", "check"], log)


def main():
    OUTPUT.mkdir(exist_ok=True)
    mode = sys.argv[1]
    if mode not in {"sources", "validate"}:
        raise SystemExit("Expected sources or validate")
    stages = [("fetch-sources", fetch_sources)] if mode == "sources" else [
        ("install-app-code", prepare_apps),
        ("install-mail-site", lambda log: install_site("mail", log)),
        ("install-suite-site", lambda log: install_site("suite", log)),
        ("repeated-migrations", migrate_sites),
        ("production-assets", build_apps),
        ("doctype-controllers", check_controllers),
        ("focused-workflows", check_workflows),
        ("same-site-install-guards", check_install_guards),
        ("cross-site-namespace-isolation", check_namespaces),
        ("final-site-and-dependency-state", record_sites),
    ]
    results = []
    try:
        for name, operation in stages:
            result = {"stage": name, "status": "running"}
            results.append(result)
            with (OUTPUT / (name + ".log")).open("w") as log:
                operation(log)
            result["status"] = "passed"
            print(json.dumps(result), flush=True)
    except Exception as error:
        result.update(status="failed", error=str(error))
        print((OUTPUT / (name + ".log")).read_text()[-16000:], flush=True)
        raise
    finally:
        (OUTPUT / (mode + "-results.json")).write_text(json.dumps(results, indent=2) + "\n")
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("| Stage | Result |\n|---|---|\n")
            for row in results:
                summary.write(f"| {row['stage']} | {row['status']} |\n")


if __name__ == "__main__":
    main()
