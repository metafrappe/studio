"""Exercise Studio on the disposable v16 CI site without paid AI requests."""

import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import frappe

from studio.ai import llm
from studio.build import StudioAppBuilder
from studio.studio.doctype.studio_page.studio_page import get_page


def run():
	if os.environ.get("CI") != "true" or frappe.local.site != "test_studio":
		raise RuntimeError("This smoke test only runs on the disposable test_studio CI site")
	check = TestCase()
	frappe.set_user("Administrator")
	app = frappe.get_doc({
		"doctype": "Studio App", "app_title": "V16 Smoke App", "app_name": "v16-smoke-app",
		"is_standard": 0,
	}).insert()
	page = frappe.get_doc({
		"doctype": "Studio Page", "studio_app": app.name, "page_title": "Home",
		"route": "/home", "published": 0, "blocks": "[]",
	}).insert()
	blocks = frappe.as_json([{
		"componentId": "v16-root", "componentName": "div", "children": [{
			"componentId": "v16-button", "componentName": "Button",
			"componentProps": {"label": "Frappe v16"}, "children": [],
		}],
	}])
	page.save_draft(blocks, known_modified=str(page.modified))
	check.assertEqual(page.reload().draft_blocks, blocks)
	page.publish(known_modified=str(page.modified))
	check.assertEqual(get_page(app.name, "/home")["blocks"], blocks)
	print("PASS: create app, save draft and publish page")

	frappe.set_user("Guest")
	with check.assertRaises(frappe.DoesNotExistError):
		get_page(app.name, "/home")
	with check.assertRaises(frappe.PermissionError):
		get_page(app.name, "/home", preview=True)
	frappe.set_user("Administrator")
	page.allow_guest = 1
	page.save()
	frappe.set_user("Guest")
	check.assertEqual(get_page(app.name, "/home")["name"], page.name)
	frappe.set_user("Administrator")
	page.unpublish()
	frappe.set_user("Guest")
	with check.assertRaises(frappe.DoesNotExistError):
		get_page(app.name, "/home")
	frappe.set_user("Administrator")
	page.publish()
	print("PASS: private, public, preview and unpublished page permissions")

	builder = StudioAppBuilder(app.name, is_standard=False)
	builder.build()
	check.assertTrue(any(Path(builder.out_dir).rglob("*.js")))
	print("PASS: build a published application bundle")

	check_ai_adapter(check)
	frappe.db.commit()


def check_ai_adapter(check):
	# Use LiteLLM's real mock response path; block network transport as a backstop.
	with (
		patch("httpx.Client.send", side_effect=AssertionError("AI test must not use the network")),
		patch("requests.sessions.Session.request", side_effect=AssertionError("AI test must not use the network")),
	):
		messages = [{"role": "user", "content": "CI compatibility check"}]
		params = {"mock_response": "v16 ready"}
		result = llm.complete("openai/gpt-4o-mini", messages, params, stream=False, api_key="ci-not-a-key")
		check.assertEqual(result, "v16 ready")
		chunks = llm.complete("openai/gpt-4o-mini", messages, params, stream=True, api_key="ci-not-a-key")
		text = "".join(chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices)
		check.assertEqual(text, "v16 ready")
		tools = [{"type": "function", "function": {
			"name": "get_page", "description": "Read a test page",
			"parameters": {"type": "object", "properties": {}},
		}}]
		response = llm.complete_with_tools(
			"openai/gpt-4o-mini", messages, tools, params, api_key="ci-not-a-key",
		)
		check.assertEqual(response.choices[0].message.content, "v16 ready")
	print("PASS: LiteLLM completion, streaming and tool-capable adapter without network requests")
