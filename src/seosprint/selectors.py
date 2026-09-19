from __future__ import annotations

import re

START_PATTERNS = [
    re.compile(r"начать\s+выполнение", re.I),
    re.compile(r"start\s+execution", re.I),
    re.compile(r"^начать$", re.I),
]

SUBMIT_PATTERNS = [
    re.compile(r"отправить\s+отч", re.I),
    re.compile(r"отправить\s+на\s+проверку", re.I),
    re.compile(r"^отправить$", re.I),
    re.compile(r"submit", re.I),
]

START_SELECTORS = [
    "a.btn-ok",
    ".btn-dock a.btn-ok",
    "a[onclick*='task-use']",
    "a[onclick*='formSubmit']",
]

REPORT_FORM_SELECTORS = [
    "#task-report",
    "form[id*='report']",
    "form[name*='report']",
]

REPORT_SELECTORS = [
    "#task_report",
    "textarea[name='task_report']",
    "textarea[name='report']",
    "textarea[name='text']",
    "textarea[name='message']",
    "textarea#report",
    "textarea.report-text",
    "#task-report textarea",
    "form textarea",
    "textarea",
]

SUBMIT_SELECTORS = [
    "a.btn-ok",
    ".btn-dock a.btn-ok",
    "input[type='submit']",
    "button[type='submit']",
]

FILE_INPUT_SELECTORS = [
    "input[type='file']",
    "input[name='file']",
    "input[name='screenshot']",
    "input[name='image']",
]
