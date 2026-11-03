"""Fixed Linux Coding program. Imported for its source only; never executed on the API host."""
import base64
import grp
import hashlib
import json
import os
import pwd
import re
import resource
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

CASES = ((2, 8, 8), (-7, -3, -3), (4, 4, 4))
ADAPTER = '''public final class AEInvokeMax {
 public static void main(String[] args) throws ReflectiveOperationException {
  java.lang.reflect.Method method = Max.class.getDeclaredMethod("max", int.class, int.class);
  int modifiers = method.getModifiers();
  if (!java.lang.reflect.Modifier.isPublic(Max.class.getModifiers())
      || !java.lang.reflect.Modifier.isPublic(modifiers)
      || !java.lang.reflect.Modifier.isStatic(modifiers)
      || method.getReturnType() != int.class
      || method.getExceptionTypes().length != 0) {
   throw new IllegalStateException("Expected public static int Max.max(int,int)");
  }
  System.out.print(Max.max(Integer.parseInt(args[0]),Integer.parseInt(args[1])));
 }
}'''
ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/workspace", "LANG": "C.UTF-8"}
LAUNCHER = Path("/opt/ae-java-verifier/launch.py")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def command(arguments, *, seconds=10):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen([sys.executable, "-I", str(LAUNCHER), *arguments], cwd="/workspace", env=ENV,
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, close_fds=True,
                                   start_new_session=True, user=1000, group=1000, extra_groups=[])
        timed_out = False
        try:
            process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        stdout.seek(0)
        stderr.seek(0)
        out, err = stdout.read(32769), stderr.read(32769)
    return {"exit_code": process.returncode, "timed_out": timed_out,
            "truncated": len(out) > 32768 or len(err) > 32768,
            "stdout_sha256": sha(out), "stderr_sha256": sha(err)}, out, err


def main(payload):
    if os.getuid() != 0 or set(payload) != {"source_base64", "source_sha256"}:
        raise ValueError("Fixed root supervisor and source binding are required")
    source = base64.b64decode(payload["source_base64"], validate=True)
    if not source or len(source) > 65536 or sha(source) != payload["source_sha256"]:
        raise ValueError("Candidate source digest differs")
    source.decode("utf-8")
    # Numeric Unix credentials need no passwd record. Never adopt a conflicting existing named identity.
    try:
        account = pwd.getpwuid(1000)
    except KeyError:
        account = None
    if account is not None and (account.pw_name != "ae-verifier" or pwd.getpwnam(account.pw_name).pw_uid != 1000 or account.pw_gid != 1000):
        raise RuntimeError("UID1000 conflicts with another identity")
    try:
        group = grp.getgrgid(1000)
    except KeyError:
        group = None
    if group is not None and group.gr_name != "ae-verifier":
        raise RuntimeError("GID1000 conflicts with another identity")
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    workspace = Path("/workspace")
    workspace.mkdir(exist_ok=True)
    os.chown(workspace, 1000, 1000)
    os.chmod(workspace, 0o700)
    comparator = Path(__file__)
    before = sha(comparator.read_bytes())
    network_receipt, network_out, _ = command(["--probe"])
    if network_receipt["exit_code"] != 0 or network_receipt["timed_out"] or network_receipt["truncated"]:
        raise RuntimeError("Inherited network isolation probe failed")
    isolation = json.loads(network_out)
    if isolation["policy_sha256"] != sha(LAUNCHER.read_bytes()):
        raise RuntimeError("Isolation source changed")
    probe = ("import json,os;from pathlib import Path;blocked=False;parent_blocked=False\n"
             "try:Path(" + repr(str(comparator)) + ").write_text('tampered')\n"
             "except PermissionError:blocked=True\n"
             "try:Path(" + repr(str(comparator.parent)) + ").rename('/workspace/replaced-private')\n"
             "except PermissionError:parent_blocked=True\n"
             "p=Path('/workspace/.permission-probe');p.write_text('ok');p.unlink()\n"
             "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'blocked':blocked,'cwd':os.getcwd(),"
             "'workspace_writable':True,'parent_replace_blocked':parent_blocked}))")
    receipt, out, _ = command([sys.executable, "-I", "-c", probe])
    protection = json.loads(out)
    if (receipt["exit_code"] != 0 or receipt["timed_out"] or receipt["truncated"]
            or protection != {"uid": 1000, "gid": 1000, "blocked": True, "cwd": "/workspace",
                              "workspace_writable": True, "parent_replace_blocked": True}
            or sha(comparator.read_bytes()) != before):
        raise RuntimeError("Protected comparator probe failed")
    result = {"schema_version": 1, "protocol": "java-max-v2", "source_sha256": sha(source), "isolation": isolation,
              "program_sha256": before, "protection": {**protection, "comparator_unchanged": True},
              "tests_sha256": sha(json.dumps(CASES, separators=(",", ":")).encode()),
              "compile": None, "cases": [], "passed": False}
    for executable in ("javac", "java"):
        version, out, err = command([executable, "-version"])
        text = (out + err).decode("utf-8", "replace")
        pattern = r"^javac 21\." if executable == "javac" else r'^(?:openjdk|java) version "21\.'
        if version["exit_code"] != 0 or not re.search(pattern, text):
            raise RuntimeError("Reviewed JDK21 is unavailable")
        result[executable + "_version_sha256"] = sha(out + err)
    base = comparator.parent.parent
    candidate, adapters = base / "candidate-classes", base / "adapter-classes"
    candidate.mkdir(mode=0o700)
    os.chown(candidate, 1000, 1000)
    adapters.mkdir(mode=0o700)
    os.chown(adapters, 1000, 1000)
    source_file = workspace / "Max.java"
    source_file.write_bytes(source)
    os.chmod(source_file, 0o444)
    compilation, _, _ = command(["javac", "-proc:none", "-encoding", "UTF-8", "-d", str(candidate), str(source_file)],
                                 seconds=20)
    result["compile"] = compilation
    if compilation["exit_code"] != 0 or compilation["timed_out"] or compilation["truncated"]:
        return result
    # Candidate code cannot mutate later tests or compiled classes between fresh JVMs.
    for path in candidate.rglob("*"):
        os.chown(path, 0, 0)
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chown(candidate, 0, 0)
    os.chmod(candidate, 0o555)
    adapter_source = base / "adapter-source"
    adapter_source.mkdir(mode=0o755)
    adapter = adapter_source / "AEInvokeMax.java"
    adapter.write_text(ADAPTER, encoding="utf-8")
    os.chmod(adapter, 0o444)
    os.chmod(adapter_source, 0o555)
    adapter_compile, _, _ = command(["javac", "-proc:none", "-cp", str(candidate), "-d", str(adapters), str(adapter)],
                                     seconds=20)
    result["adapter_compile"] = adapter_compile
    if adapter_compile["exit_code"] != 0 or adapter_compile["timed_out"] or adapter_compile["truncated"]:
        return result  # An incompatible public signature is a known candidate failure.
    for path in adapters.rglob("*"):
        os.chown(path, 0, 0)
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chown(adapters, 0, 0)
    os.chmod(adapters, 0o555)
    class_files = [adapters / "AEInvokeMax.class", candidate / "Max.class"]
    class_before = [sha(path.read_bytes()) for path in class_files]
    class_probe = ("import json;from pathlib import Path;blocked=[]\n"
                   "for name in " + repr([str(path) for path in class_files]) + ":\n"
                   " try:Path(name).write_bytes(b'tampered');blocked.append(False)\n"
                   " except PermissionError:blocked.append(True)\n"
                   "print(json.dumps(blocked))")
    probe_receipt, out, _ = command([sys.executable, "-I", "-c", class_probe])
    if (probe_receipt["exit_code"] != 0 or json.loads(out) != [True, True]
            or class_before != [sha(path.read_bytes()) for path in class_files]):
        raise RuntimeError("Protected compiled classes probe failed")
    result["class_protection"] = {"adapter_blocked": True, "candidate_blocked": True, "digests_unchanged": True}
    for index, (a, b, expected) in enumerate(CASES):
        execution, out, _ = command(["java", "-Xmx128m", "-cp", str(adapters) + ":" + str(candidate),
                                      "AEInvokeMax", str(a), str(b)], seconds=5)
        passed = (execution["exit_code"] == 0 and not execution["timed_out"] and not execution["truncated"]
                  and out == str(expected).encode())
        result["cases"].append({"case_index": index, "passed": passed, **execution,
                                "expected_sha256": sha(str(expected).encode())})
    if sha(comparator.read_bytes()) != before or class_before != [sha(path.read_bytes()) for path in class_files]:
        raise RuntimeError("Protected verifier state changed during candidate execution")
    result["passed"] = all(row["passed"] for row in result["cases"]) and len(result["cases"]) == 3
    return result


if __name__ == "__main__":
    print(json.dumps(main(json.loads(base64.b64decode(sys.argv[1], validate=True))), allow_nan=False))
