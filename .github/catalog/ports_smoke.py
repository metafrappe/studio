"""Focused regressions for the v16 ports. External AI/AWS calls are simulated."""
import importlib
import io
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.model.base_document import get_controller


def run(app):
    assert app in frappe.get_installed_apps()
    modules = frappe.get_all('Module Def', filters={'app_name': app}, pluck='name')
    doctypes = frappe.get_all('DocType', filters={'module': ['in', modules]}, pluck='name')
    for doctype in doctypes:
        frappe.get_meta(doctype)
        get_controller(doctype)
    globals()['check_' + app]()
    print({'app': app, 'controllers': len(doctypes), 'regressions': 'passed'})


def run_unittests(module):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(module)))
    assert result.testsRun > 0 and result.wasSuccessful(), module


def check_ecommerce_integrations():
    import boto3
    from moto import mock_aws
    from requests import Request
    from ecommerce_integrations.amazon.doctype.amazon_sp_api_settings.amazon_sp_api import SPAPI
    with mock_aws():
        api = SPAPI('arn:aws:iam::123456789012:role/TestRole', 'client', 'secret', 'refresh', 'testing', 'testing')
        auth = api.get_auth()
        request = Request('GET', 'https://sellingpartnerapi-na.amazon.com/orders/v0/orders').prepare()
        auth(request)
        assert request.headers['Authorization'].startswith('AWS4-HMAC-SHA256')
        assert 'X-Amz-Security-Token' in request.headers


def check_doppio():
    from click.testing import CliRunner
    from doppio.commands import add_spa, add_desk_page
    for command in [add_spa, add_desk_page]:
        result = CliRunner().invoke(command, ['--help'])
        assert result.exit_code == 0, result.output
        assert '--app' in result.output


def check_suite():
    run_unittests('suite.suite_drive.webdav.tests.test_xmlutil')
    from suite.writer.api import docs
    from suite.suite_core.boot import before_install
    payload = b'---\ntitle: Demo\n---\n# Hello\n\n**World**\n\n[[Example]]'
    result = {}
    with patch.object(docs, 'FileManager') as manager:
        manager.return_value.get_file.return_value = io.BytesIO(payload)
        docs.get_markdown_file(frappe._dict(name='test'), result)
    assert '<h1>Hello</h1>' in result['file_content'], result
    assert '<strong>World</strong>' in result['file_content']
    assert result['properties']['title'] == ['Demo']
    assert 'get_wiki_link?title=Example' in result['file_content']
    with patch('frappe.get_installed_apps', return_value=['frappe', 'drive']):
        try: before_install()
        except frappe.ValidationError: pass
        else: raise AssertionError('Suite must reject standalone Drive on the same site')
    assert frappe.get_module_app('Suite Drive') == 'suite'
    assert frappe.get_module_app('Suite Meet') == 'suite'


def check_flow():
    run_unittests('flow.tests.test_ai_model')


def check_expenses():
    import expenses.libs
    from expenses.libs.logger import get_logger
    assert get_logger('info') is get_logger('info')
    doc = frappe.get_single('Expenses Settings')
    assert doc.doctype == 'Expenses Settings'
    for name in ['Expense', 'Expenses Request', 'Expenses Entry']:
        assert frappe.new_doc(name).doctype == name


def check_pdf_on_submit():
    run_unittests('pdf_on_submit.tests.test_quill')


def check_oidc_extended():
    from oidc_extended.callback import custom
    from frappe.utils.oauth import create_oauth_state, consume_oauth_state
    with patch('oidc_extended.callback.get_info_via_oauth') as exchange, patch('frappe.respond_as_web_page') as response:
        custom('unused', 'unrecognized-state')
        exchange.assert_not_called()
        assert response.call_args.kwargs['http_status_code'] == 417
        state = create_oauth_state('/app')
        assert consume_oauth_state(state) == '/app'
        custom('unused', state)
        exchange.assert_not_called()
        assert response.call_args.kwargs['http_status_code'] == 417


def check_nextassist():
    from claude_agent_sdk import AssistantMessage, TextBlock
    from nextassist.ai import claude_code_provider as module
    from nextassist.database.pool import test_connection
    from nextassist.database.settings_db import get_settings, save_settings
    assert test_connection()
    save_settings({'enable_tool_calling': True, 'enable_file_uploads': False})
    assert not get_settings()['enable_file_uploads']
    save_settings({'enable_tool_calling': True, 'enable_file_uploads': True})
    provider = module.ClaudeCodeProvider(SimpleNamespace(get_password=lambda key: 'ci-placeholder'))
    options = provider._build_options('test-model', 'test system')
    assert options.model == 'test-model' and options.system_prompt == 'test system'
    assert options.env['ANTHROPIC_API_KEY'] == 'ci-placeholder'
    async def mock_query(*, prompt, options):
        assert prompt == 'Hello'
        yield AssistantMessage(content=[TextBlock(text='Merhaba')], model='test-model')
    with patch.object(module, 'query', mock_query):
        response = provider.chat_completion([{'role': 'user', 'content': 'Hello'}], model='test-model')
    assert response['content'] == 'Merhaba'


def check_s3(app, settings):
    import boto3
    from moto import mock_aws
    controller = importlib.import_module(app + '.controller')
    # Installing the adapter alone must not try to reach AWS or alter local files.
    with patch.object(controller, 'S3Operations') as storage:
        local_file = frappe.get_doc({'doctype': 'File', 'file_name': 'unconfigured.txt', 'content': b'local', 'is_private': 1}).insert()
        assert local_file.file_url.startswith('/private/files/')
        local_file.delete()
        storage.assert_not_called()
    with mock_aws():
        s3 = boto3.client('s3', region_name='us-east-1', aws_access_key_id='testing', aws_secret_access_key='testing')
        s3.create_bucket(Bucket='v16-test-bucket')
        config = frappe.get_single(settings)
        config.bucket_name = 'v16-test-bucket'
        config.region_name = 'us-east-1'
        config.aws_key = 'testing'
        config.aws_secret = 'testing'
        config.delete_file_from_cloud = 1
        config.save()
        file_doc = frappe.get_doc({'doctype':'File','file_name':'v16 test & private.txt','content':b'private content','is_private':1}).insert()
        frappe.db.commit()
        key = file_doc.content_hash
        assert s3.get_object(Bucket='v16-test-bucket', Key=key)['Body'].read() == b'private content'
        controller.generate_file(key)
        assert frappe.response.type == 'redirect'
        assert 'X-Amz-Signature=' in frappe.response.location
        email = 'v16-s3-reader@example.invalid'
        if not frappe.db.exists('User', email):
            frappe.get_doc({'doctype':'User','email':email,'first_name':'Unrelated Reader','send_welcome_email':0}).insert()
        frappe.set_user(email)
        try:
            try: controller.generate_file(key)
            except frappe.PermissionError: pass
            else: raise AssertionError('Private S3 files must require File read permission')
            try: controller.migrate_existing_files()
            except frappe.PermissionError: pass
            else: raise AssertionError('Bulk file migration must require System Manager')
        finally:
            frappe.set_user('Administrator')
        file_doc.delete()
        frappe.db.commit()
        assert s3.list_objects_v2(Bucket='v16-test-bucket')['KeyCount'] == 0
        with patch.object(controller, 'S3Operations') as storage:
            controller.delete_from_cloud(frappe._dict(file_url='/private/files/local.txt', content_hash='a'*32))
            storage.assert_not_called()
        config.bucket_name = ''
        config.save()
        frappe.db.commit()
    other = 'frappe_s3_improwised' if app == 'frappe_s3_attachment' else 'frappe_s3_attachment'
    with patch('frappe.get_installed_apps', return_value=['frappe', other]):
        try: importlib.import_module(app + '.install').before_install()
        except frappe.ValidationError: pass
        else: raise AssertionError('Only one storage adapter may be installed per site')


def check_frappe_s3_attachment():
    check_s3('frappe_s3_attachment', 'S3 File Attachment')


def check_frappe_s3_improwised():
    check_s3('frappe_s3_improwised', 'Improwised S3 File Attachment')
