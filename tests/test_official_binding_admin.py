"""Synthetic framework-route/admin integration; never imports the plugin/WebUI host.

Only pure source definitions, fresh temp SQLite/config and memory peers are
used. No login, real credentials, user records or network are accessed.
"""
import ast
import asyncio
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json
import re
import sqlite3
from types import SimpleNamespace
from threading import Event, Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_official_binding_recovery as recovery_tests
import test_official_readonly as prior

admin_module = prior.module("official_binding_admin")
bindings = recovery_tests.bindings
ENV, GROUP, USER = recovery_tests.ENV, recovery_tests.GROUP, recovery_tests.USER


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.case = recovery_tests.RecoveryTests("test_committed_code_and_all_effects_recover_without_another_reserve")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.fixture, self.allowed, self.now = self.case.fixture, True, 100.0
        self.admin = admin_module.OfficialBindingAdmin(self.fixture.host, self.case.worker,
            authorize=lambda environment: self.allowed and environment == ENV, clock=lambda: self.now)

    def scope(self):
        return dict(environment_id=ENV, config_id=self.fixture.group["config_id"], expected_revision=self.fixture.group["revision"])

    def inspect(self, operation_id=None):
        return self.admin.inspect(**self.scope(), operation_id=operation_id or self.fixture.operation()["operation_id"])

    def act(self, detail, selected_action, **changes):
        values = dict(**self.scope(), operation_id=detail["operation_id"], expected_state=detail["state"],
                      payload_hash=detail["payload_hash"], action=selected_action, confirmed=True)
        values.update(changes)
        return self.admin.act(**values)

    def prepared_unbind(self):
        self.fixture.execute()
        event = replace(self.fixture.event, message_id="SYNTHETIC_ADMIN_PENDING")
        with patch.object(self.fixture.center, "commit_unbind", side_effect=bindings.BindingError("RESULT_UNKNOWN", "synthetic lost ACK")):
            self.fixture.execute("unbind", "all", event)
        return self.fixture.operation(event, "unbind_user")

    def test_missing_or_revoked_framework_guard_blocks_before_center_configuration(self):
        for guard in (None, lambda environment: False):
            admin = admin_module.OfficialBindingAdmin(self.fixture.host, self.case.worker, authorize=guard)
            with self.assertRaises(bindings.BindingError): admin.list(**self.scope())
        self.assertEqual(self.fixture.opens, 0)

    def test_host_composes_management_guard_before_settings_and_into_connection_guard(self):
        with self.assertRaises(bindings.BindingError): self.fixture.host.recovery(guard=lambda: False)
        self.assertEqual(self.fixture.opens, 0)
        guards = []
        original = self.fixture.host.center_factory
        def capture(settings, guard):
            guards.append(guard)
            return original(settings, guard)
        with patch.object(self.fixture.host, "center_factory", side_effect=capture):
            self.admin._context(**self.scope())
        self.allowed = False
        with self.assertRaises(bindings.BindingError): guards[0]()

    def test_list_and_detail_omit_actor_user_token_credentials_and_intent(self):
        self.fixture.execute()
        page = self.admin.list(**self.scope())
        detail = self.inspect()
        encoded = json.dumps(dict(page=page, detail=detail))
        for forbidden in (USER, "123456", "SYNTHETIC_DB_USER", "SYNTHETIC_NOT_A_REAL_PASSWORD",
                          "token_generation", "reservation_id", "nickname_basis", "actor_id", "intent"):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(detail["state"], "COMMITTED")
        self.assertEqual(detail["delivery"][0]["whitelist"], "PENDING")
        self.assertTrue(page["operations"][0]["updated_at"].endswith("Z"))

    def test_read_only_inspection_does_not_contact_the_mod(self):
        self.fixture.execute()
        before = list(self.case.peer().calls)
        self.admin.list(**self.scope())
        detail = self.inspect(self.fixture.operation()["operation_id"][:8])
        self.assertEqual(detail["operation_id"], self.fixture.operation()["operation_id"])
        self.assertEqual(self.case.peer().calls, before)

    def test_nonactive_environment_or_stale_group_revision_cannot_load_settings(self):
        self.fixture.current = {"platform": "onebot"}
        with self.assertRaises(bindings.BindingError): self.admin.list(**self.scope())
        self.fixture.current = dict(prior.SELECTION)
        values = self.scope()
        self.fixture.update({"alias": "Changed alias"})
        with self.assertRaises(bindings.BindingError): self.admin.list(**values)
        self.assertEqual(self.fixture.opens, 0)

    def test_foreign_group_operation_is_not_exposed(self):
        self.fixture.execute()
        with closing(sqlite3.connect(self.fixture.db_path)) as db:
            db.execute("UPDATE bindings_qqofficial_ops SET group_id='SYNTHETIC_OTHER_GROUP'")
            db.commit()
        operation_id = prior.module("official_binding_commands").request_identity(self.fixture.event, "bind")[1]
        with self.assertRaises(bindings.BindingError): self.inspect(operation_id)

    def test_pending_retry_cannot_commit_or_fabricate_a_requester_event(self):
        operation = self.prepared_unbind()
        detail = self.inspect(operation["operation_id"])
        before = list(self.case.peer().calls)
        with patch.object(self.fixture.center, "commit_unbind", side_effect=prior.forbidden):
            with self.assertRaises(bindings.BindingError): self.act(detail, "retry")
        self.assertEqual(self.fixture.center.get_operation(ENV, operation["operation_id"], group_id=GROUP)["state"], "PREPARED")
        self.assertEqual(self.case.peer().calls, before)

    def test_cancel_cas_keeps_original_actor_and_account_and_never_contacts_mod(self):
        operation = self.prepared_unbind()
        detail = self.inspect(operation["operation_id"])
        before = list(self.case.peer().calls)
        result = self.act(detail, "cancel")
        stored = self.fixture.center.get_operation(ENV, operation["operation_id"], group_id=GROUP)
        self.assertEqual(result["code"], "OK")
        self.assertEqual(stored["state"], "ABORTED")
        self.assertEqual(stored["actor_id"], USER)
        self.assertEqual(self.fixture.center.active_snapshot(ENV, GROUP, USER)[0]["revision"], "1")
        self.assertEqual(self.case.peer().calls, before)

    def test_terminal_retry_uses_real_mirror_ack_adapter_without_rebinding(self):
        self.fixture.execute()
        detail = self.inspect()
        before = self.case.peer().calls.count("reserve")
        result = self.act(detail, "retry")
        self.assertEqual(result["code"], "OK")
        self.assertFalse(result["operation"]["pending"])
        self.assertEqual(result["operation"]["delivery"][0]["whitelist"], "APPLIED")
        self.assertEqual(self.case.peer().calls.count("reserve"), before)
        self.assertEqual(self.fixture.center.active_snapshot(ENV, GROUP, USER)[0]["revision"], "1")

    def test_partial_ack_remains_pending_instead_of_claiming_complete(self):
        self.case.peer().whitelist = "PENDING"
        self.fixture.execute()
        result = self.act(self.inspect(), "retry")
        self.assertEqual(result["code"], "SYNC_PENDING")
        self.assertEqual(result["operation"]["delivery"][0]["mirror"], "APPLIED")
        self.assertEqual(result["operation"]["delivery"][0]["whitelist"], "PENDING")

    def test_background_gate_blocks_duplicate_manual_retry_without_dispatch(self):
        self.fixture.execute()
        detail = self.inspect()
        gate = self.case.worker.gate
        gate.acquire()
        try:
            with self.assertRaises(bindings.BindingError): self.act(detail, "retry")
        finally:
            gate.release()
        self.assertNotIn("sync", self.case.peer().calls)

    def test_hash_state_confirmation_and_action_are_checked_before_mutation(self):
        operation = self.prepared_unbind()
        detail = self.inspect(operation["operation_id"])
        for values in (dict(payload_hash="f" * 64), dict(expected_state="RESERVED"),
                       dict(confirmed=1), dict(confirmed=False), dict(action="commit"), dict(expected_state={})):
            with self.assertRaises(bindings.BindingError): self.act(detail, "cancel", **values)
        self.assertEqual(self.fixture.center.get_operation(ENV, operation["operation_id"], group_id=GROUP)["state"], "PREPARED")

    def test_committed_result_cannot_be_cancelled_or_rolled_back(self):
        self.fixture.execute()
        detail = self.inspect()
        with self.assertRaises(bindings.BindingError): self.act(detail, "cancel")
        self.assertEqual(self.fixture.operation()["state"], "COMMITTED")

    def test_late_commit_wins_cancel_and_is_reported_as_conflict(self):
        operation = self.prepared_unbind()
        detail = self.inspect(operation["operation_id"])
        original = self.fixture.center.abort
        def late(**values):
            self.fixture.center.commit_unbind(environment_id=ENV, group_id=GROUP, operation_id=operation["operation_id"],
                                               policy_revision=str(self.fixture.group["revision"]))
            return original(**values)
        with patch.object(self.fixture.center, "abort", side_effect=late):
            result = self.act(detail, "cancel")
        self.assertEqual(result["code"], "CONFLICT")
        self.assertEqual(result["operation"]["state"], "COMMITTED")

    def test_unknown_abort_result_is_not_retried(self):
        operation = self.prepared_unbind()
        detail = self.inspect(operation["operation_id"])
        with patch.object(self.fixture.center, "abort", side_effect=bindings.BindingError("RESULT_UNKNOWN", "synthetic ACK lost")) as abort:
            with self.assertRaises(bindings.BindingError): self.act(detail, "cancel")
        self.assertEqual(abort.call_count, 1)

    def test_summary_cursor_is_snapshot_bound_and_checked_against_filter_and_revision(self):
        self.fixture.execute()
        summary = self.admin.list(**self.scope())["operations"][0]
        rows = [dict(summary, operation_id=str(uuid4())) for _ in range(26)]
        with patch.object(self.fixture.center, "operation_summaries", return_value=rows):
            first = self.admin.list(**self.scope(), pending=False)
        second = self.admin.list(**self.scope(), pending=False, cursor=first["next_cursor"])
        self.assertEqual(len(first["operations"]), 20)
        self.assertEqual(second["operations"], rows[20:])
        with self.assertRaises(bindings.BindingError):
            self.admin.list(**self.scope(), pending=True, cursor=first["next_cursor"])
        self.now += 601
        with self.assertRaises(bindings.BindingError):
            self.admin.list(**self.scope(), pending=False, cursor=first["next_cursor"])

    def test_summary_capacity_is_explicitly_truncated_and_cache_has_a_bound(self):
        self.fixture.execute()
        summary = self.admin.list(**self.scope())["operations"][0]
        rows = [dict(summary, operation_id=str(uuid4())) for _ in range(201)]
        with patch.object(self.fixture.center, "operation_summaries", return_value=rows) as read:
            for _ in range(18): page = self.admin.list(**self.scope())
        self.assertTrue(page["truncated"])
        self.assertEqual(page["total"], 200)
        self.assertEqual(len(self.admin.pages), 16)
        self.assertEqual(read.call_args.kwargs["limit"], 201)

    def test_summary_query_does_not_include_another_group_or_completed_record(self):
        self.fixture.execute()
        self.act(self.inspect(), "retry")
        self.assertEqual(self.admin.list(**self.scope())["operations"], [])
        self.assertEqual(len(self.admin.list(**self.scope(), pending=False)["operations"]), 1)

    def test_corrupt_summary_time_or_invalid_limit_is_rejected(self):
        self.fixture.execute()
        with closing(sqlite3.connect(self.fixture.db_path)) as db:
            db.execute("UPDATE bindings_qqofficial_ops SET updated_at='SYNTHETIC_PRIVATE_INVALID_TIME'")
            db.commit()
        with self.assertRaises(bindings.BindingError): self.admin.list(**self.scope())
        with self.assertRaises(bindings.BindingError): self.fixture.center.operation_summaries(ENV, GROUP, limit=202)

    def test_permission_revocation_or_close_invalidates_existing_context(self):
        context, group = self.admin._context(**self.scope())
        self.allowed = False
        with self.assertRaises(bindings.BindingError): context.group_guard(group["group_openid"])
        self.admin.close()
        self.assertEqual(len(self.admin.pages), 0)

    def test_close_does_not_wait_for_a_query_holding_the_cache_lock(self):
        entered, release, stopped = Event(), Event(), Event()
        def query():
            with self.admin.lock:
                entered.set()
                release.wait(3)
        def stop():
            self.admin.close()
            stopped.set()
        first, second = Thread(target=query), Thread(target=stop)
        first.start()
        try:
            self.assertTrue(entered.wait(1))
            second.start()
            self.assertTrue(stopped.wait(1))
            with self.assertRaises(bindings.BindingError): self.admin.list(**self.scope())
        finally:
            release.set()
            first.join(2)
            if second.ident is not None: second.join(2)


class RouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse((prior.ROOT / "web" / "api.py").read_text(encoding="utf-8-sig"))
        names = {"_official_operation_query", "_official_operation_result", "view_official_operations",
                 "view_official_operation", "view_official_operation_action"}
        functions = [deepcopy(node) for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.calls = []
        async def body(): return self.body, ""
        def method(**values): self.calls.append(values); return {"synthetic": True}
        def validate(*args): pass
        self.admin = SimpleNamespace(list=method, inspect=method, act=method, validate_scope=validate)
        runtime = SimpleNamespace(binding_admin=self.admin)
        self.query = dict(environment_id=ENV, config_id="22222222-2222-4222-8222-222222222222", expected_revision="2")
        self.namespace = dict(asyncio=asyncio, re=re, OfficialError=prior.core.OfficialError,
            web_request=SimpleNamespace(query=self.query), official_runtime=SimpleNamespace(current=lambda: runtime),
            _ok=lambda value: {"status": "ok", "data": value}, _err=lambda value: {"status": "error", "message": value}, _body=body)
        exec(compile(ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])), "<synthetic-official-web-routes>", "exec"), self.namespace)
        self.tree = tree

    async def test_registered_routes_use_existing_framework_extension_surface(self):
        routes = next(node.value for node in self.tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ROUTES" for t in node.targets))
        methods = {item.elts[0].value: ast.literal_eval(item.elts[2]) for item in routes.elts}
        self.assertEqual(methods["official/operations"], ["GET"])
        self.assertEqual(methods["official/operation"], ["GET"])
        self.assertEqual(methods["official/operation/action"], ["POST"])

    async def test_get_scope_is_strict_and_calls_off_event_loop(self):
        self.query["filter"] = "all"
        result = await self.namespace["view_official_operations"]()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(self.calls[0]["pending"])
        self.assertEqual(self.calls[0]["expected_revision"], 2)
        self.query["actor"] = "SYNTHETIC_FORGED_ADMIN"
        result = await self.namespace["view_official_operations"]()
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(self.calls), 1)

    async def test_action_rejects_actor_or_new_binding_claims_before_dispatch(self):
        self.body = dict(self.query, operation_id=str(uuid4()), action="cancel", expected_state="PREPARED", payload_hash="a" * 64, confirmed=True)
        self.body["actor"] = {"kind": "admin"}
        result = await self.namespace["view_official_operation_action"]()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.calls, [])

    async def test_late_scope_revocation_prevents_returning_worker_data(self):
        def revoked(*args): raise prior.core.OfficialError("synthetic scope revoked")
        self.admin.validate_scope = revoked
        result = await self.namespace["view_official_operations"]()
        self.assertEqual(result["status"], "error")
        self.assertNotIn("synthetic", result.get("data", {}))

    async def test_private_unexpected_exception_is_sanitized(self):
        def unavailable(**values): raise RuntimeError("SYNTHETIC_PRIVATE_SETTINGS")
        self.admin.list = unavailable
        result = await self.namespace["view_official_operations"]()
        self.assertEqual(result["status"], "error")
        self.assertNotIn("SYNTHETIC_PRIVATE_SETTINGS", str(result))

    async def test_real_admin_service_matches_registered_http_field_names(self):
        fixture = AdminTests("test_terminal_retry_uses_real_mirror_ack_adapter_without_rebinding")
        await asyncio.to_thread(fixture.setUp)
        self.addCleanup(fixture.doCleanups)
        await asyncio.to_thread(fixture.fixture.execute)
        self.query.clear()
        self.query.update({key: str(value) for key, value in fixture.scope().items()})
        self.namespace["official_runtime"] = SimpleNamespace(current=lambda: SimpleNamespace(binding_admin=fixture.admin))
        result = await self.namespace["view_official_operations"]()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["data"]["operations"]), 1)
        detail = await asyncio.to_thread(fixture.inspect)
        self.body = dict(fixture.scope(), operation_id=detail["operation_id"], action="retry",
                         expected_state=detail["state"], payload_hash=detail["payload_hash"], confirmed=True)
        result = await self.namespace["view_official_operation_action"]()
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["data"]["operation"]["pending"])
