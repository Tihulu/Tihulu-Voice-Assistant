# SPDX-License-Identifier: AGPL-3.0-only
"""Actual disposable Firefox WebDriver smoke test, no owner browser or internet.

Fixed local HTML, private browser profile, isolated network namespace. This
does not authorize runtime browsing, arbitrary URLs, user actions or scripts.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, parse_qs

HTML=b"""<!doctype html><meta charset=utf-8><title>TIHULU Fixture</title>
<p id="message">Public synthetic browser fixture</p>
<button id="change" onclick="document.getElementById('message').textContent='Changed locally'">Change</button>
"""

class WebDriverError(RuntimeError):
    def __init__(self,status,error,message):
        super().__init__(f"webdriver {status} {error}: {message[:1000]}")
        self.status,self.error,self.message=status,error,message

def denied_navigation(error,target):
    if type(error) is not WebDriverError or error.status!=500:
        return False
    if error.error not in ("unknown error","timeout"):
        return False
    # Gecko reports the privileged about:neterror URL in an unknown error.
    # A bare error string is insufficient: bind the exact attempted address.
    # Require a closed network error code and the exact attempted destination.
    marker="about:neterror?"
    prefix="Reached error page: "
    if not error.message.startswith(prefix+marker) or len(error.message)>4096:
        return False
    raw=error.message[len(prefix):]
    if any(c.isspace() for c in raw):
        return False
    try:
        parts=urlsplit(raw)
        params=parse_qs(parts.query,keep_blank_values=True,max_num_fields=16)
    except ValueError:
        return False
    return (parts.scheme=="about" and parts.path=="neterror" and not parts.fragment
            and params.get("e") in (["connectionFailure"],["netTimeout"])
            and params.get("u")==[target])

def child(browser,driver,host_net,host_port):
    sys.path.insert(0,"/work/fixture-code")
    from tihulu_jarvis import profiles
    from gateway import Context, FixtureGateway, Refused
    from offline_browser import OfflineBrowser
    results=[]
    def record(case,ok,**details):
        results.append(bool(ok))
        print(json.dumps(dict(case=case,status="PASS" if ok else "FAIL",**details)),flush=True)
    record("browser-separate-network-namespace",os.readlink("/proc/self/ns/net")!=host_net)
    record("browser-private-home",os.environ.get("HOME")=="/tmp" and not Path("/work/not-approved").exists())
    try:
        with socket.create_connection(("127.0.0.1",int(host_port)),timeout=1):
            host_denied=False
    except OSError:
        host_denied=True
    record("browser-host-loopback-denied",host_denied)
    if not all(results):
        return 1
    version=subprocess.run([browser,"--version"],stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8,check=False)
    print(json.dumps({"firefox_version_exit":version.returncode,
                      "stdout":version.stdout.decode("utf-8","replace")[:1000],
                      "stderr":version.stderr.decode("utf-8","replace")[:2000]}),flush=True)
    if version.returncode:
        print(json.dumps({"case":"browser-loader","status":"HOLD"}),flush=True)
        return 1
    Path("/tmp/browser-runtime").mkdir(mode=0o700)
    os.environ["XDG_RUNTIME_DIR"]="/tmp/browser-runtime"
    def command(method,path,payload=None,*,timeout=8):
        conn=http.client.HTTPConnection("127.0.0.1",4444,timeout=timeout)
        try:
            body=None if payload is None else json.dumps(payload,separators=(",",":")).encode()
            conn.request(method,path,body=body,headers={"Content-Type":"application/json"})
            response=conn.getresponse()
            data=response.read(65537)
            if len(data)>65536:
                raise RuntimeError("webdriver response limit")
            result=json.loads(data)
            if type(result) is not dict or "value" not in result:
                raise RuntimeError("invalid webdriver response")
            value=result["value"]
            if response.status!=200:
                if (type(value) is not dict or type(value.get("error")) is not str
                        or type(value.get("message")) is not str):
                    raise RuntimeError("invalid webdriver error")
                raise WebDriverError(response.status,value["error"],value["message"])
            return value
        finally:
            conn.close()
    with open("/tmp/geckodriver.log","wb") as log:
        proc=subprocess.Popen([driver,"--host","127.0.0.1","--port","4444","--log","warn"],
                              stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        session=None
        owned=None
        phase="driver_ready"
        try:
            deadline=time.monotonic()+10
            while True:
                if proc.poll() is not None or time.monotonic()>=deadline:
                    raise RuntimeError("webdriver unavailable")
                try:
                    if command("GET","/status",timeout=1).get("ready"):
                        break
                except (OSError,RuntimeError,http.client.HTTPException):
                    pass
                time.sleep(.1)
            phase="session_start"
            value=command("POST","/session",{"capabilities":{"alwaysMatch":{
                "browserName":"firefox","moz:firefoxOptions":{"binary":browser,
                    "args":["-headless","-no-remote"],
                    "prefs":{"browser.shell.checkDefaultBrowser":False,
                             "browser.startup.page":0,"datareporting.policy.dataSubmissionEnabled":False,
                             "toolkit.telemetry.enabled":False}}}}},timeout=30)
            session=value.get("sessionId")
            if type(session) is not str or not re.fullmatch(r"[a-fA-F0-9-]{1,64}",session):
                raise RuntimeError("unexpected session id")
            base="/session/"+session
            caps=value.get("capabilities",{})
            profile=caps.get("moz:profile","")
            record("browser-private-profile",caps.get("browserName")=="firefox" and
                   type(profile) is str and profile.startswith("/tmp/"),
                   version=caps.get("browserVersion","unknown"))
            if not all(results):
                return 1
            phase="local_navigation"
            command("POST",base+"/timeouts",{"pageLoad":10000,"script":5000,"implicit":0})
            version=caps.get("browserVersion")
            source_hash=hashlib.sha256(Path("/work/fixture.html").read_bytes()).hexdigest()
            ctx=Context("browser-fixture","session","profile",1,"local","LOCAL_ONLY")
            rules=tuple(profiles.Rule(x,(),"allow") for x in
                        ("browser_navigate","filesystem_read","host_shell"))
            gate=FixtureGateway(ctx,rules,clock=profiles.PolicyClock())
            owned=OfflineBrowser(gate,ctx,version,session,"tab","fixture-html",source_hash)
            def start(operation,identity,element=None):
                call=owned.prepare(operation,element)
                grant=gate.fixture_grant(identity,call.request(ctx),60)
                owned.begin(call,grant)
                owned.checkpoint()
            start("offline_open","open")
            command("POST",base+"/url",{"url":"file:///work/fixture.html"},timeout=15)
            owned.complete()
            record("browser-exact-local-navigation",command("GET",base+"/url")=="file:///work/fixture.html"
                   and command("GET",base+"/title")=="TIHULU Fixture")
            def element(identity):
                value=command("POST",base+"/element",{"using":"css selector","value":"#"+identity})
                element_id=value.get("element-6066-11e4-a52e-4f735466cecf")
                if type(element_id) is not str or not re.fullmatch(r"[a-fA-F0-9-]{1,64}",element_id):
                    raise RuntimeError("unexpected element id")
                return base+"/element/"+element_id
            start("offline_read","read","message")
            text=command("GET",element("message")+"/text")
            owned.complete(text)
            record("browser-real-dom-text",text=="Public synthetic browser fixture")
            record("browser-exact-read-receipt",owned.result()==hashlib.sha256(text.encode()).hexdigest())
            if not all(results):
                return 1
            phase="local_click"
            start("offline_click","click","change")
            command("POST",element("change")+"/click",{})
            owned.complete()
            start("offline_read","after","message")
            text=command("GET",element("message")+"/text")
            owned.complete(text)
            record("browser-fixed-local-click",text=="Changed locally")
            record("browser-exact-post-click-receipt",owned.result()==hashlib.sha256(text.encode()).hexdigest())
            gate.revoke("open")
            try:
                start("offline_click","revoked","change")
            except Refused:
                denied=True
            else:
                denied=False
            record("browser-revoked-parent-blocks-operation",denied)
            owned.close()
            try:
                owned.result()
            except Refused:
                closed=True
            else:
                closed=False
            record("browser-closed-fixture-receipt-denied",closed)
            if not all(results):
                return 1
            # Fixed disposable host-loopback listener only, not external URLs.
            # A real browser navigation must not reach it across namespaces.
            phase="denied_navigation"
            target="http://127.0.0.1:"+host_port+"/fixture"
            try:
                command("POST",base+"/url",{"url":target},timeout=15)
            except WebDriverError as exc:
                denied=denied_navigation(exc,target)
                diagnostic={"error":exc.error,"message":exc.message[:1500],"http":exc.status}
            else:
                denied=False
                diagnostic={"returned_url":command("GET",base+"/url"),
                            "title":command("GET",base+"/title")}
            record("browser-real-host-navigation-denied",denied,**diagnostic)
            phase="session_close"
            command("DELETE",base)
            session=None
            record("browser-session-closed",True)
            try:
                command("GET",base+"/title")
            except RuntimeError as exc:
                closed="invalid session id" in str(exc).lower()
            else:
                closed=False
            record("browser-closed-session-unusable",closed)
        except (RuntimeError,OSError,http.client.HTTPException) as exc:
            print(json.dumps({"case":"browser-webdriver-session","status":"HOLD",
                              "phase":phase,"reason":str(exc)[:1000]}),flush=True)
            with open("/tmp/geckodriver.log","rb") as detail:
                print(detail.read(4096).decode("utf-8","replace"),flush=True)
            return 1
        finally:
            if owned is not None:
                owned.close()
            if session:
                try:
                    command("DELETE","/session/"+session)
                except (OSError,RuntimeError,http.client.HTTPException):
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    return 0 if len(results)==14 and all(results) else 1

def main():
    tools=[]
    for name in ("firefox","geckodriver"):
        found=shutil.which(name)
        if not found:
            print(json.dumps({"case":"browser-tool-availability","status":"HOLD","missing":name}))
            return 1
        path=Path(found).resolve()
        if name=="firefox":
            # Hosted Ubuntu 22.04 package wrapper calls which through the
            # host alternatives tree. Use only the fixed actual package ELF
            # in the already read-only /usr, never mount /etc/alternatives.
            candidate=Path("/usr/lib/firefox/firefox")
            if (path!=Path("/usr/lib/firefox/firefox.sh") and path!=candidate
                    or not candidate.is_file() or candidate.resolve()!=candidate
                    or candidate.stat().st_uid!=0):
                print(json.dumps({"case":"browser-package-binary","status":"HOLD",
                                  "resolved":str(path)}))
                return 1
            with candidate.open("rb") as handle:
                if handle.read(4)!=b"\x7fELF":
                    print(json.dumps({"case":"browser-package-binary","status":"HOLD"}))
                    return 1
            path=candidate
        if not str(path).startswith("/usr/") or not path.is_file():
            print(json.dumps({"case":"browser-tool-availability","status":"HOLD","tool":name}))
            return 1
        tools.append(str(path))
    if os.getuid()==0:
        raise RuntimeError("root fixture prohibited")
    with tempfile.TemporaryDirectory(prefix="jarvis-browser-") as temp:
        root=Path(temp)
        page=root/"fixture.html"
        page.write_bytes(HTML)
        marker=root/"not-approved"
        marker.write_text("public hidden marker",encoding="ascii")
        uid,gid=os.getuid(),os.getgid()
        (root/"passwd").write_text(f"fixture:x:{uid}:{gid}:Fixture:/tmp:/usr/sbin/nologin\n")
        (root/"group").write_text(f"fixture:x:{gid}:\n")
        (root/"nsswitch.conf").write_text("passwd: files\ngroup: files\nhosts: files\n")
        (root/"fonts.conf").write_text('<?xml version="1.0"?><fontconfig>'
            '<dir>/usr/share/fonts/truetype/dejavu</dir><cachedir>/tmp/font-cache</cachedir></fontconfig>')
        prefix=["/usr/bin/bwrap","--unshare-all","--die-with-parent","--new-session",
                "--cap-drop","ALL","--clearenv","--ro-bind","/usr","/usr"]
        for name in ("bin","sbin","lib","lib64"):
            path=Path("/"+name)
            if path.is_symlink():
                target=os.readlink(path)
                if target not in ("usr/"+name,"/usr/"+name):
                    raise RuntimeError("unexpected system layout")
                prefix+=["--symlink",target,"/"+name]
            elif path.exists():
                raise RuntimeError("non-merged system layout")
        prefix+=["--proc","/proc","--dev","/dev","--tmpfs","/dev/shm","--tmpfs","/tmp",
                 "--dir","/work","--dir","/etc",
                 "--ro-bind",str(page),"/work/fixture.html",
                 "--ro-bind",str(Path(__file__).resolve()),"/work/runner.py",
                 "--ro-bind",str(root/"passwd"),"/etc/passwd",
                 "--ro-bind",str(root/"group"),"/etc/group",
                 "--ro-bind",str(root/"nsswitch.conf"),"/etc/nsswitch.conf",
                 "--ro-bind",str(root/"fonts.conf"),"/work/fonts.conf",
                 "--setenv","HOME","/tmp","--setenv","PATH","/usr/bin:/usr/local/bin",
                 "--setenv","LC_ALL","C.UTF-8","--setenv","FONTCONFIG_FILE","/work/fonts.conf",
                 "--setenv","MOZ_HEADLESS","1","--setenv","USER","fixture",
                 "--setenv","LD_LIBRARY_PATH","/usr/lib/firefox:/usr/lib/x86_64-linux-gnu/pulseaudio",
                 "--chdir","/work","--remount-ro","/"]
        # Copy only the exact reviewed pure Python dependency files. Never
        # bind repository metadata, owner home, full source tree or credentials.
        repo=Path(__file__).resolve().parents[1]
        code=root/"fixture-code"
        names=("gateway.py","capabilities.py","offline_browser.py","adapter_protocol.py","inference.py")
        for name in names:
            dst=code/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo/"experiments"/name,dst)
        for name in ("__init__.py","profiles.py","selected_csv.py","temperature_engine.py",
                     "voice/__init__.py","voice/m1.py"):
            dst=code/"tihulu_jarvis"/name
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(repo/"src/tihulu_jarvis"/name,dst)
        prefix[-2:-2]=["--ro-bind",str(code),"/work/fixture-code"]
        host_net=os.readlink("/proc/self/ns/net")
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1",0))
            if listener.getsockname()[1]==4444:
                raise RuntimeError("fixture port collision")
            listener.listen(4)
            port=str(listener.getsockname()[1])
            result=subprocess.run(prefix+["--","/usr/bin/python3","-I","/work/runner.py","--child",*tools,host_net,port],
                                  stdin=subprocess.DEVNULL,timeout=115,check=False,
                                  env={"PATH":"/usr/bin:/bin","LC_ALL":"C.UTF-8"})
            listener.setblocking(False)
            try:
                connection,_=listener.accept()
            except BlockingIOError:
                untouched=True
            else:
                connection.close()
                untouched=False
            print(json.dumps({"case":"browser-host-listener-untouched",
                              "status":"PASS" if untouched else "FAIL"}),flush=True)
        unchanged=hashlib.sha256(page.read_bytes()).digest()==hashlib.sha256(HTML).digest()
        print(json.dumps({"case":"browser-source-unchanged","status":"PASS" if unchanged else "FAIL"}),flush=True)
        return 0 if result.returncode==0 and unchanged and untouched else 1

if __name__=="__main__":
    raise SystemExit(child(*sys.argv[2:]) if len(sys.argv)==6 and sys.argv[1]=="--child" else main())
