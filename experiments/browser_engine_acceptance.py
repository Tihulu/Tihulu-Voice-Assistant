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

HTML=b"""<!doctype html><meta charset=utf-8><title>TIHULU Fixture</title>
<p id="message">Public synthetic browser fixture</p>
<button id="change" onclick="document.getElementById('message').textContent='Changed locally'">Change</button>
"""

def child(browser,driver,host_net,host_port):
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
            if response.status!=200 or len(data)>65536:
                raise RuntimeError("webdriver status "+str(response.status)+": "+data[:500].decode("utf-8","replace"))
            result=json.loads(data)
            if type(result) is not dict or "value" not in result:
                raise RuntimeError("invalid webdriver response")
            return result["value"]
        finally:
            conn.close()
    with open("/tmp/geckodriver.log","wb") as log:
        proc=subprocess.Popen([driver,"--host","127.0.0.1","--port","4444","--log","warn"],
                              stdin=subprocess.DEVNULL,stdout=log,stderr=log)
        session=None
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
            command("POST",base+"/url",{"url":"file:///work/fixture.html"},timeout=15)
            record("browser-exact-local-navigation",command("GET",base+"/url")=="file:///work/fixture.html"
                   and command("GET",base+"/title")=="TIHULU Fixture")
            def element(identity):
                value=command("POST",base+"/element",{"using":"css selector","value":"#"+identity})
                element_id=value.get("element-6066-11e4-a52e-4f735466cecf")
                if type(element_id) is not str or not re.fullmatch(r"[a-fA-F0-9-]{1,64}",element_id):
                    raise RuntimeError("unexpected element id")
                return base+"/element/"+element_id
            record("browser-real-dom-text",command("GET",element("message")+"/text")==
                   "Public synthetic browser fixture")
            if not all(results):
                return 1
            phase="local_click"
            command("POST",element("change")+"/click",{})
            record("browser-fixed-local-click",command("GET",element("message")+"/text")=="Changed locally")
            # Fixed disposable host-loopback listener only, not external URLs.
            # A real browser navigation must not reach it across namespaces.
            phase="denied_navigation"
            try:
                command("POST",base+"/url",{"url":"http://127.0.0.1:"+host_port+"/fixture"},timeout=15)
            except RuntimeError as exc:
                denied=("NS_ERROR_CONNECTION_REFUSED" in str(exc)
                        or "NS_ERROR_NET_TIMEOUT" in str(exc))
            else:
                denied=False
            record("browser-real-host-navigation-denied",denied)
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
    return 0 if len(results)==10 and all(results) else 1

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
