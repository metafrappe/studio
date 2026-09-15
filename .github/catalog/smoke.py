import frappe
from frappe.model.base_document import get_controller


def run(app):
    assert app in frappe.get_installed_apps(), app
    modules = frappe.get_all("Module Def", filters={"app_name": app}, pluck="name")
    doctypes = frappe.get_all("DocType", filters={"module": ["in", modules]}, pluck="name")
    for doctype in doctypes:
        frappe.get_meta(doctype)
        get_controller(doctype)
    print({"app": app, "modules": len(modules), "controllers_imported": len(doctypes)})
