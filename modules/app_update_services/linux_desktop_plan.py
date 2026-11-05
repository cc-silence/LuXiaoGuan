"""Adapt the native transaction plan to the shared update center contract."""
from pathlib import Path
from .linux_install_plan import plan as transaction_plan, catalog_files
from modules.platform_services.linux_update_files import RootedFiles


def make_plan(root,catalog,cancel=lambda:False,cache=None):
    def check():
        if cancel():raise InterruptedError('Linux update cancelled')
    with RootedFiles(root) as files:
        value=transaction_plan(files,catalog,check,collect_conflicts=True)
    entries=catalog_files(catalog);downloads=[];removed=[]
    for row in value['operations']:
        item=entries.get(row['path'])
        if item is not None and item['kind']=='file':downloads.append(dict(item,before=row['before']))
        if row['after'] is None:removed.append(row)
    return dict(files=downloads,removed=removed,conflicts=value['conflicts'],
                unchanged=value['unchanged']+len(value['preserved']),preserved=value['preserved'],
                download_bytes=sum(item['blob_size'] for item in downloads),version=catalog['version'])
