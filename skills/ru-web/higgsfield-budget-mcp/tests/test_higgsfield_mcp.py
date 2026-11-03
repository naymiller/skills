"""Офлайн-тесты адаптера на локальной заглушке API (без сети, без реального ключа, без списаний)."""
import json, os, subprocess, sys, tempfile, threading, time, unittest, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server" / "higgsfield_mcp.py"
FAKE_KEY = "test-key-id:test-key-secret-not-real"


class Mock(BaseHTTPRequestHandler):
    state = {"by_key": {}, "requests": {}, "fail_next_post": 0, "posts": 0}

    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, raw=None):
        body = raw if raw is not None else (json.dumps(obj).encode() if obj is not None else b"")
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        return self.headers.get("Authorization") == f"Key {FAKE_KEY}"

    def do_GET(self):
        if self.path.startswith("/files/"):
            assert "Authorization" not in self.headers, "ключ ушёл на адрес файла"
            return self._send(200, raw=b"PNGDATA")
        if not self._auth():
            return self._send(401, {"detail": "Invalid credentials"})
        rid = self.path.split("/")[2]
        r = self.state["requests"].get(rid)
        if not r:
            return self._send(404, {"detail": "not found"})
        port = self.server.server_address[1]
        return self._send(200, {"status": "completed", "request_id": rid,
                                "images": [{"url": f"http://127.0.0.1:{port}/files/{rid}.png"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if not self._auth():
            return self._send(401, {"detail": "Invalid credentials"})
        if self.path.startswith("/estimate/"):
            return self._send(200, {"credits": "1.500", "usd": "0.094"})
        if self.path.endswith("/cancel"):
            return self._send(400, {"detail": "started"})
        self.state["posts"] += 1
        if self.state["fail_next_post"]:
            self.state["fail_next_post"] -= 1
            # запрос принят, но ответ «потерян»
            key = self.headers["Idempotency-Key"]
            self.state["by_key"].setdefault(key, str(uuid.uuid4()))
            self.state["requests"][self.state["by_key"][key]] = body
            return self._send(502, {"detail": "bad gateway"})
        key = self.headers.get("Idempotency-Key")
        rid = self.state["by_key"].setdefault(key, str(uuid.uuid4()))
        self.state["requests"][rid] = body
        self._send(200, {"status": "queued", "request_id": rid})


class Client:
    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, env=env)
        self.i = 0
        self.call_raw("initialize", {"protocolVersion": "2024-11-05"})
        self.log = []

    def call_raw(self, method, params=None):
        self.i += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.i, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, _tool, **args):
        r = self.call_raw("tools/call", {"name": _tool, "arguments": args})["result"]
        text = r["content"][0]["text"]
        self.log.append(text)
        return (json.loads(text) if not r.get("isError") else None), text, bool(r.get("isError"))

    def close(self):
        self.p.stdin.close()
        self.p.wait(5)


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.tmp = tempfile.mkdtemp()
        base = {k: v for k, v in os.environ.items() if not k.startswith("HF_")}
        base.update(HF_API_BASE=f"http://127.0.0.1:{cls.httpd.server_address[1]}", HFMCP_STATE_DIR=cls.tmp + "/state", HFMCP_WORKSPACE=cls.tmp + "/ws",
                    HFMCP_KEYCHAIN_SERVICE="hfmcp-test-none-" + uuid.uuid4().hex)
        os.makedirs(cls.tmp + "/ws", exist_ok=True)
        cls.base = dict(base)
        cls.env_nokey = dict(base)
        cls.env = dict(base, HF_KEY=FAKE_KEY)
        cls.env_bad = dict(base, HF_KEY="bad:key")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_01_no_key(self):
        c = Client(self.env_nokey)
        r, _, _ = c.tool("hf_status")
        self.assertFalse(r["credential_configured"])
        _, text, err = c.tool("hf_estimate", model="higgsfield-ai/soul/v2/standard", arguments={"prompt": "x"})
        self.assertTrue(err)
        self.assertIn("не настроен", text)
        c.close()

    def test_02_bad_key_401(self):
        c = Client(self.env_bad)
        r, _, _ = c.tool("hf_status")
        self.assertIn("401", r["auth_check"])
        c.close()

    def test_03_flow_budget_idempotency_download(self):
        c = Client(self.env)
        r, _, _ = c.tool("hf_status")
        self.assertIn("ok", r["auth_check"])
        _, text, err = c.tool("hf_submit", batch_id="nope", model="higgsfield-ai/soul/v2/standard", arguments={"prompt": "x"})
        self.assertTrue(err and "пакет" in text)
        b, _, _ = c.tool("hf_batch_open", name="t", max_usd=0.2, max_requests=3)
        # 1) первая отправка: сервер «теряет» ответ дважды → адаптер повторяет с тем же ключом → одна генерация
        Mock.state["fail_next_post"] = 2
        before = len(Mock.state["requests"])
        s1, text1, err1 = c.tool("hf_submit", batch_id=b["batch_id"], model="higgsfield-ai/soul/v2/standard",
                                 arguments={"prompt": "t-shirt"})
        self.assertFalse(err1, text1)
        self.assertEqual(len(Mock.state["requests"]) - before, 1, "дубль генерации")
        # 2) повтор с тем же intent_id → без новой отправки
        posts = Mock.state["posts"]
        s1b, _, _ = c.tool("hf_submit", batch_id=b["batch_id"], model="higgsfield-ai/soul/v2/standard",
                           arguments={"prompt": "t-shirt"}, intent_id=s1["intent_id"])
        self.assertTrue(s1b["already_submitted"])
        self.assertEqual(Mock.state["posts"], posts)
        # 3) вторая генерация укладывается (0.188 ≤ 0.2), третья — превышение лимита
        s2, _, err2 = c.tool("hf_submit", batch_id=b["batch_id"], model="higgsfield-ai/soul/v2/standard", arguments={"prompt": "b"})
        self.assertFalse(err2)
        _, text3, err3 = c.tool("hf_submit", batch_id=b["batch_id"], model="higgsfield-ai/soul/v2/standard", arguments={"prompt": "c"})
        self.assertTrue(err3 and "Превышение лимита" in text3)
        # 4) статус и скачивание
        g, _, _ = c.tool("hf_get", request_id=s1["request_id"])
        self.assertEqual(g["status"], "completed")
        _, text, err = c.tool("hf_download", request_id=s1["request_id"], output_dir="/tmp/outside-workspace")
        self.assertTrue(err and "только внутри" in text)
        out = "projects/test/outputs/images"
        d, _, _ = c.tool("hf_download", request_id=s1["request_id"], output_dir=out, prefix="tee")
        self.assertEqual(Path(d["files"][0]["path"]).read_bytes(), b"PNGDATA")
        meta = Path(d["meta"]).read_text()
        self.assertNotIn("http", meta)
        # 5) отмена уже начатого
        x, _, _ = c.tool("hf_cancel", request_id=s2["request_id"])
        self.assertFalse(x["canceled"])
        # 6) журнал переживает перезапуск
        c.close()
        c2 = Client(self.env)
        jobs, _, _ = c2.tool("hf_jobs")
        self.assertTrue(any(j["request_id"] == s1["request_id"] for j in jobs))
        st, _, _ = c2.tool("hf_batch_status", batch_id=b["batch_id"])
        self.assertEqual(st["requests"], 2)
        # 7) ключ нигде не светится
        secret_half = FAKE_KEY.split(":")[1]
        everything = "\n".join(c.log + c2.log)
        for f in Path(self.tmp).rglob("*"):
            if f.is_file():
                everything += f.read_text(errors="ignore")
        self.assertNotIn(secret_half, everything)
        self.assertNotIn(FAKE_KEY.split(":")[0], everything)
        c2.close()

    def test_06_workspace_init_and_jail(self):
        home = Path(tempfile.mkdtemp())
        env = {k: v for k, v in self.base.items() if k not in ("HFMCP_WORKSPACE", "HFMCP_STATE_DIR")}
        env.update(HOME=str(home), HF_KEY=FAKE_KEY)
        (home / "secret.txt").write_text("confidential")
        (home / "Downloads").mkdir()
        c = Client(env)
        st, _, _ = c.tool("hf_status")
        self.assertIsNone(st["workspace"])
        _, text, err = c.tool("hf_upload", path=str(home / "secret.txt"))
        self.assertTrue(err and "не создана" in text)
        for bad in ("~", "~/Downloads", "/tmp/x", "/"):
            _, _, err = c.tool("hf_workspace_init", path=bad)
            self.assertTrue(err, bad)
        r, _, err = c.tool("hf_workspace_init")
        self.assertFalse(err)
        ws = home / "HiggsfieldWork"
        self.assertTrue(r["created"] and (ws / "references").is_dir())
        r2, _, _ = c.tool("hf_workspace_init", path="~/Other")
        self.assertFalse(r2["created"])
        self.assertEqual(r2["workspace"], str(ws.resolve()))
        _, text, err = c.tool("hf_upload", path=str(home / "secret.txt"))
        self.assertTrue(err and "только внутри" in text)
        _, text, err = c.tool("hf_upload", path=str(ws / "references" / ".." / ".." / "secret.txt"))
        self.assertTrue(err and "только внутри" in text)
        os.symlink(home / "secret.txt", ws / "references" / "link.png")
        _, text, err = c.tool("hf_upload", path=str(ws / "references" / "link.png"))
        self.assertTrue(err and "только внутри" in text)
        c.close()

    def test_04_bad_model_and_tools_list(self):
        c = Client(self.env)
        tools = c.call_raw("tools/list")["result"]["tools"]
        self.assertEqual(len(tools), 12)
        _, text, err = c.tool("hf_estimate", model="../../etc/passwd", arguments={})
        self.assertTrue(err)
        c.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
