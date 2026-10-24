"""文件拖放意图转为既有后台任务，界面只负责确认和冻结参数。"""
import stat


def handle_file_drop(page, source_remote, target_remote, entries, folder=None):
    """同栏移动到文件夹；跨栏复制到当前目录或所指文件夹。"""
    if not page._require_connection():return
    entries=entries if isinstance(entries,(list,tuple)) else [entries]
    if not entries:return
    if source_remote!=target_remote:
        destination=folder['path'] if folder else None
        page._transfer(source_remote,entries,destination=destination)
        return
    if folder is None:
        page._notify('请拖到目标文件夹上。','info')
        return
    if (not stat.S_ISDIR(folder['mode']) or any(entry['path']==folder['path'] for entry in entries)
            or any(not (stat.S_ISREG(entry['mode']) or stat.S_ISDIR(entry['mode'])) for entry in entries)):
        page._notify('不能移入自身，也不移动链接或特殊文件。','warning')
        return
    title='移动所选项目'
    note='将 '+str(len(entries))+' 项移动到：'+folder['path']+'\n遇到同名项目将停止，不覆盖、不合并；已经完成的移动保留。'
    if not page._confirm(note):return
    jobs=[]
    for entry in entries:
        jobs.append(dict(operation='remote_move' if target_remote else 'local_move',name=entry['name'],size=1,
            arguments=dict(path=entry['path'],parent=page._current_path[target_remote],folder=folder['path'],
                           expected=entry['identity'],folder_expected=folder['identity'])))
    page._submit_batch(jobs,target_remote,title)


def external_upload_paths(view, event):
    """Accept only local URL payloads on the connected router pane; no disk IO."""
    if not view.remote or not view.file_page.service.manager.ssh.connected:
        return ()
    mime = event.mimeData()
    if mime.hasFormat(view.MIME) or not mime.hasUrls():
        return ()
    urls = mime.urls()
    if not 1 <= len(urls) <= 10000 or any(not url.isLocalFile() for url in urls):
        return ()
    paths = tuple(url.toLocalFile() for url in urls)
    return paths if all(paths) else ()


def begin_external_upload(page, paths, folder=None):
    """Freeze connection and destination before asynchronous local inspection."""
    destination = folder['path'] if folder else page._current_path[True]
    connection = page.service.manager.ssh._client
    page._submit('local_upload_selection', ('external_upload', destination, connection), paths=paths)


def complete_external_upload(page, purpose, entries):
    ssh = page.service.manager.ssh
    if not ssh.connected or ssh._client is not purpose[2]:
        return
    page._transfer(False, entries, destination=purpose[1])
