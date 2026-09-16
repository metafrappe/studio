"""Validate the cargo and payment apps together on a disposable MariaDB site."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


WORKSPACE = Path(os.environ["GITHUB_WORKSPACE"])
TEMPORARY = Path(os.environ["RUNNER_TEMP"])
BENCH = TEMPORARY / "frappe-bench"
OUTPUT = WORKSPACE / "cargo-payments-results"
PINS = json.loads((WORKSPACE / ".github/catalog/cargo-payments-pins.json").read_text())
BASELINE_PINS = json.loads((WORKSPACE / ".github/catalog/mail-pins.json").read_text())
SITE = "test_cargo_payments"
CORE_PINS = {
	"frappe": "988e54f3c4c291e2077a83809663f123731abe76",
	"erpnext": "4048fb70e14d1843956fcdabb7c3cca75a1cbcdd",
	"hrms": "a4768b441cff346def505e27f2a2229ee1e05b9b",
	"payments": "cca07d9f9392e2ea0e521c5975151db9e4b6c321",
}


def run(command, log, cwd=BENCH, timeout=900):
	log.write("\n$ " + " ".join(map(str, command)) + "\n")
	log.flush()
	subprocess.run(
		command,
		cwd=cwd,
		stdout=log,
		stderr=subprocess.STDOUT,
		check=True,
		timeout=timeout,
		env=os.environ.copy(),
	)


def fetch_sources(log):
	# Mail/Suite manifests extend the existing 54-app dependency snapshot.
	# Only the two new apps are installed into this focused integration site.
	sources = {**BASELINE_PINS, **PINS}
	for app, candidate in sources.items():
		assert re.fullmatch(r"[0-9a-f]{40}", candidate["sha"]), (app, candidate)
		source = TEMPORARY / ("source_" + app)
		run(
			[
				"git",
				"clone",
				"--depth",
				"1",
				"--branch",
				candidate["branch"],
				"https://github.com/" + candidate["repo"] + ".git",
				str(source),
			],
			log,
			WORKSPACE,
		)
		run(["git", "fetch", "--depth", "1", "origin", candidate["sha"]], log, source)
		run(["git", "checkout", "--detach", candidate["sha"]], log, source)
		actual = subprocess.check_output(
			["git", "rev-parse", "HEAD"], cwd=source, text=True
		).strip()
		assert actual == candidate["sha"], (app, actual, candidate["sha"])
	(OUTPUT / "source-pins.json").write_text(json.dumps(sources, indent=2) + "\n")


def prepare_apps(log):
	for app in PINS:
		run(["bench", "get-app", "--skip-assets", app, str(TEMPORARY / ("source_" + app))], log)
		actual = subprocess.check_output(
			["git", "rev-parse", "HEAD"], cwd=BENCH / "apps" / app, text=True
		).strip()
		assert actual == PINS[app]["sha"], (app, actual)
	run(["bench", "pip", "check"], log)
	run(["bench", "version"], log)


def install_site(log):
	run(
		[
			"bench",
			"new-site",
			SITE,
			"--db-host",
			"127.0.0.1",
			"--db-port",
			"3306",
			"--db-root-username",
			"root",
			"--db-root-password",
			"ci_root",
			"--mariadb-user-host-login-scope",
			"%",
			"--admin-password",
			"ci_admin",
		],
		log,
	)
	for key in ("allow_tests", "mute_emails", "pause_scheduler"):
		run(["bench", "--site", SITE, "set-config", key, "True", "--parse"], log)
	for app in ("erpnext", "payments", "hrms", *PINS):
		run(["bench", "--site", SITE, "install-app", app], log)


def migrate_site(log):
	run(["bench", "--site", SITE, "migrate"], log)


def migrate_twice(log):
	for _ in range(2):
		migrate_site(log)


def build_apps(log):
	for app in PINS:
		run(["bench", "build", "--app", app], log, timeout=1800)


def check_controllers(log):
	for app in PINS:
		run(
			[
				"bench",
				"--site",
				SITE,
				"execute",
				"frappe._catalog_smoke.run",
				"--kwargs",
				json.dumps({"app": app}),
			],
			log,
		)


def check_workflows(log):
	for app in PINS:
		run(
			[
				"bench",
				"--site",
				SITE,
				"execute",
				app + ".tests.v16_smoke.run",
				"--kwargs",
				json.dumps({"app": app}),
			],
			log,
		)


def check_after_migration(log):
	migrate_site(log)
	check_controllers(log)
	check_workflows(log)


def record_site(log):
	source_pins = {}
	for app, expected_sha in {
		**CORE_PINS,
		**{app: row["sha"] for app, row in PINS.items()},
	}.items():
		actual = subprocess.check_output(
			["git", "rev-parse", "HEAD"], cwd=BENCH / "apps" / app, text=True
		).strip()
		assert actual == expected_sha, (app, actual, expected_sha)
		source_pins[app] = actual
	(OUTPUT / "installed-source-pins.json").write_text(json.dumps(source_pins, indent=2) + "\n")
	expected = ["frappe", "erpnext", "payments", "hrms", *PINS]
	code = (
		"import frappe, json, sys; frappe.init(sys.argv[1]); frappe.connect(); "
		"installed = set(frappe.get_installed_apps()); "
		"assert set(json.loads(sys.argv[2])) == installed, installed; "
		"print(json.dumps({'installed_apps': sorted(installed), 'same_site': True})); "
		"frappe.destroy()"
	)
	run(
		[str(BENCH / "env/bin/python"), "-c", code, SITE, json.dumps(expected)],
		log,
		cwd=BENCH / "sites",
	)
	result = subprocess.run(
		["bench", "--site", SITE, "list-apps", "--format", "json"],
		cwd=BENCH,
		capture_output=True,
		text=True,
		check=True,
		timeout=60,
	)
	(OUTPUT / (SITE + "-apps.json")).write_text(result.stdout)
	log.write(result.stdout)
	run(["bench", "pip", "check"], log)


def main():
	OUTPUT.mkdir(exist_ok=True)
	mode = sys.argv[1]
	if mode not in {"sources", "validate"}:
		raise SystemExit("Expected sources or validate")
	stages = (
		[("fetch-sources", fetch_sources)]
		if mode == "sources"
		else [
			("install-app-code", prepare_apps),
			("install-combined-site", install_site),
			("repeated-migrations", migrate_twice),
			("production-assets", build_apps),
			("doctype-controllers", check_controllers),
			("shipment-and-payment-workflows", check_workflows),
			("post-workflow-migration-and-retest", check_after_migration),
			("final-site-and-dependency-state", record_site),
		]
	)
	results = []
	try:
		for name, operation in stages:
			result = {"stage": name, "status": "running"}
			results.append(result)
			print(json.dumps(result), flush=True)
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
			summary.write("\nExternal shipment and payment calls are mocked.\n")


if __name__ == "__main__":
	main()
