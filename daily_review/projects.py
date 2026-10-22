import os
from pathlib import Path
import subprocess
from .util import digest, under, workspace_path


class Projects:
    """Only git rev-parse. Never fetch, checkout, status hooks or user commands."""
    def __init__(self, config):
        self.config = config
        self.cache = {}

    def resolve(self, cwd):
        cwd = workspace_path(cwd)
        if cwd in self.cache:
            return self.cache[cwd]
        for rule in sorted(self.config.get("project_overrides", []), key=lambda x: -len(x["cwd"])):
            if under(cwd, rule["cwd"]):
                result = ("override:" + rule["key"], rule["name"])
                self.cache[cwd] = result
                return result
        key, name = "cwd:" + cwd, Path(cwd).name or cwd
        if Path(cwd).is_dir():
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
            try:
                common = subprocess.run(["git", "-C", cwd, "rev-parse", "--git-common-dir"],
                                        capture_output=True, text=True, timeout=4, env=env)
                if common.returncode == 0:
                    git = Path(common.stdout.strip())
                    git = (Path(cwd) / git).resolve() if not git.is_absolute() else git.resolve()
                    key = "git:" + str(git)
                    name = git.parent.name if git.name == ".git" else git.name.removesuffix(".git")
            except (OSError, subprocess.TimeoutExpired):
                pass
        result = (digest(key)[:20], name)
        self.cache[cwd] = result
        return result


def observed_commit(text, cwd):
    """A real commit line in tool output, scoped to the repository; never invent a hash."""
    import re
    output = text.split('返回：', 1)[-1] if '返回：' in text else ''
    match = re.search(r'^\[[^\]\r\n]+\s+([0-9a-f]{7,40})\]\s+\S', output, re.M)
    if not match:
        return None
    value = match[1]
    if len(value) == 40:
        return value
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    try:
        result = subprocess.run(['git', '-C', cwd, 'rev-parse', '--verify', value + '^{commit}'],
                                capture_output=True, text=True, timeout=4, env=env)
        full = result.stdout.strip()
        return full if result.returncode == 0 and re.fullmatch('[0-9a-f]{40}', full) else None
    except (OSError, subprocess.TimeoutExpired):
        return None
