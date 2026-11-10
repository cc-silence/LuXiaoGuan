"""Task-owned native repository copies; never change persistent router feeds."""
from contextlib import contextmanager
import re
import secrets
from .command_builder import quote_argument as q
from .package_mirror_policy import OFFICIAL,DOMESTIC
from .signal_manager import SignalError


def source_directory(token):
    """Know the task-owned path before sending a request that may lose its reply."""
    if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{32}', token):
        raise ValueError('Invalid temporary source owner')
    return '/tmp/lxg-feeds.' + token


def cleanup_script(token):
    folder = source_directory(token)
    # A pre-existing directory or symlink is never adopted by this task.
    return ('if test -e ' + q(folder) + ' || test -L ' + q(folder) + '; then '
            + 'test ! -L ' + q(folder) + ' && test -d ' + q(folder)
            + ' && test -f ' + q(folder + '/.owner')
            + ' && test ! -L ' + q(folder + '/.owner')
            + ' && test "$(cat ' + q(folder + '/.owner') + ')" = ' + q(token)
            + ' && rm -rf ' + q(folder) + '; fi')


def prepare_script(name,token,mirror=DOMESTIC[0]):
    if name not in {'opkg','apk'} or not re.fullmatch('[a-f0-9]{32}',token) or mirror not in DOMESTIC:
        raise ValueError('Invalid temporary repository request')
    rules=[]
    for source in OFFICIAL+DOMESTIC:
        for scheme in ('https://','http://'):
            root=scheme+source.split('://',1)[1]
            pattern=root.replace('.','[.]')+'/'
            rules.append('-e '+q('s@'+pattern+'@'+mirror+'/@g'))
    if name == 'opkg':
        rules.append('-e '+q(r'/^[[:space:]]*"\{0,1\}option"\{0,1\}[[:space:]][[:space:]]*"\{0,1\}\(http_proxy\|https_proxy\|ftp_proxy\)"\{0,1\}[[:space:]]/d'))
    rewrite='sed '+ ' '.join(rules)
    header="""set -e
umask 077
folder=__FOLDER__
mkdir "$folder"
trap 'rm -rf "$folder"' 0
printf '%s' __TOKEN__ > "$folder/.owner"
mkdir "$folder/feeds"
rewrite() { __REWRITE__ "$1" > "$2"; }
""".replace('__TOKEN__',q(token)).replace('__FOLDER__',q(source_directory(token))).replace('__REWRITE__',rewrite)
    if name=='opkg':
        body="""test -f /etc/opkg.conf
rewrite /etc/opkg.conf "$folder/main.conf"
count=0
for input in /etc/opkg/*.conf; do
 test -f "$input" || continue
 count=$((count+1)); test "$count" -le 64
 rewrite "$input" "$folder/feeds/${input##*/}"
done
"""
        files='"$folder/main.conf" "$folder"/feeds/*.conf'
    else:
        body=""": > "$folder/repositories"
count=0
for input in /etc/apk/repositories /etc/apk/repositories.d/*.list /lib/apk/repositories.d/*.list; do
 test -f "$input" || continue
 case "$input" in /lib/apk/repositories.d/*) test ! -e "/etc/apk/repositories.d/${input##*/}" || continue;; esac
 count=$((count+1)); test "$count" -le 64
 rewrite "$input" "$folder/current"
 cat "$folder/current" >> "$folder/repositories"
 printf '\n' >> "$folder/repositories"
done
test "$count" -gt 0
rm -f "$folder/current"
"""
        files='"$folder/repositories"'
    # Inspect URLs inside quotes/assignments too. Dynamic APK repositories use
    # the PC profile resolver; static replacement cannot prove their final host.
    check="""for input in __FILES__; do
 test -f "$input" || continue
 awk -v allowed=__MIRROR__ -v reject_variables=__VARIABLES__ '
 /^[[:space:]]*#/ {next}
 reject_variables && $1 == "set" {exit 72}
 {
  line=$0
  while (match(line, /[A-Za-z][A-Za-z0-9+.-]*:\/\//)) {
   target=substr(line,RSTART)
   if (index(target,allowed)!=1) exit 72
   line=substr(line,RSTART+RLENGTH)
  }
 }' "$input"
done
printf '%s\n' "$folder"
trap - 0
""".replace('__FILES__',files).replace('__MIRROR__',q(mirror+'/')).replace('__VARIABLES__','1' if name=='apk' else '0')
    return header+body+check


class ScopedPackageManager:
    """Decorate every update, simulation and install with the same source scope."""
    def __init__(self, base, folder):
        self.base = base
        self.folder = folder

    def __getattr__(self, name):
        return getattr(self.base, name)

    def decorate(self, command):
        if not command.startswith(self.name + ' '):
            raise ValueError('Unexpected native package command')
        if self.name == 'opkg':
            prefix = ('OPKG_CONF_DIR=' + q(self.folder + '/feeds')
                      + ' opkg -f ' + q(self.folder + '/main.conf'))
        else:
            prefix = 'apk --repositories-file ' + q(self.folder + '/repositories')
        clear_proxy = 'unset http_proxy https_proxy ftp_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy; '
        return '(' + clear_proxy + prefix + command[len(self.name):] + ')'

    def update(self):
        return self.decorate(self.base.update())

    def change(self, *args, **kwargs):
        return self.decorate(self.base.change(*args, **kwargs))


@contextmanager
def domestic_sources(manager, base, cancel, tell):
    """Use PC official/proxy transport, or an owned native domestic source copy."""
    from modules.network_policy import configured_proxy_channels
    if configured_proxy_channels():
        error = SignalError('offline_retry', '\u5df2\u914d\u7f6e\u4ee3\u7406\uff0c\u6539\u7531\u7535\u8111\u901a\u8fc7\u4ee3\u7406\u4ece\u5b98\u65b9\u6e90\u4e0b\u8f7d\u5e76\u4f20\u5305\u5b89\u88c5\u3002')
        error.route_skipped = True
        raise error
    from .signal_package_feed import preferred_mirror_order
    from .package_mirror_policy import source_name
    mirror = preferred_mirror_order()[0]
    token = secrets.token_hex(16)
    folder = source_directory(token)
    try:
        try:
            reported = manager._execute('dependency_sources_prepare', prepare_script(base.name, token, mirror), 15, cancel).strip()
            if reported != folder:
                raise ValueError('Unexpected temporary source directory')
        except (SignalError, ValueError) as exc:
            if getattr(exc, 'code', None) == 'cancelled' or (cancel is not None and cancel.is_set()):
                raise
            raise SignalError('offline_retry', '\u8def\u7531\u5668\u4e34\u65f6\u955c\u50cf\u6e90\u672a\u80fd\u5b89\u5168\u51c6\u5907\uff0c\u6539\u7531\u7535\u8111\u4e0b\u8f7d\u5e76\u6821\u9a8c\u3002') from exc
        tell('\u8def\u7531\u5668\u76f4\u8fde\u4f7f\u7528' + source_name(mirror + '/') + '\uff08\u4e34\u65f6\u914d\u7f6e\uff09\uff0c\u539f\u8f6f\u4ef6\u6e90\u4fdd\u6301\u4e0d\u53d8\u3002', 'info')
        yield ScopedPackageManager(base, folder)
    finally:
        if folder:
            command = cleanup_script(token)
            try:
                manager._execute('dependency_sources_cleanup', command, 8, None)
            except Exception:
                # An observer/cleanup error must not overwrite the installation result.
                try:
                    tell('\u4e34\u65f6\u8f6f\u4ef6\u6e90\u6e05\u7406\u672a\u786e\u8ba4\uff0c\u539f\u8f6f\u4ef6\u6e90\u672a\u4fee\u6539\u3002', 'warning')
                except Exception:
                    pass
