"""Bounded numeric SSH return-path evidence; never retain peer addresses or MACs."""

def summarize_link_probe(text):
    from .management_link import _sections
    if not isinstance(text, str) or len(text.encode('utf-8')) > 256 * 1024:
        return dict(link_report_valid=False)
    parts = _sections(text)
    result = dict(link_report_valid={'route','neigh','fdb','brctl','interfaces','end'} <= parts.keys())
    for section in ('route', 'neigh', 'fdb', 'brctl', 'interfaces'):
        result['link_' + section + '_rows'] = min(4096, sum(bool(line) for line in parts.get(section, ())))
    neighbors = [line.split() for line in parts.get('neigh', ())]
    result['link_neighbor_usable'] = sum('lladdr' in row and not {'FAILED','INCOMPLETE'} & set(row) for row in neighbors)
    macs = set()
    for row in neighbors:
        if 'lladdr' in row and not {'FAILED','INCOMPLETE'} & set(row):
            pos = row.index('lladdr') + 1
            if pos < len(row):
                macs.add(row[pos].lower())
    result['link_neighbor_mac_count'] = len(macs)
    result['link_fdb_peer_rows'] = sum(bool(row) and row[0].lower() in macs and 'dev' in row and 'self' not in row
        for row in (line.split() for line in parts.get('fdb', ())))
    result['link_brctl_peer_rows'] = sum(len(row)>=3 and row[1].lower() in macs and row[2].lower()=='no'
        for row in (line.split() for line in parts.get('brctl', ())))
    links = [line.split('|') for line in parts.get('interfaces', ())]
    result['link_bridge_count'] = sum(len(row)==6 and row[3]=='bridge' for row in links)
    result['link_port_count'] = sum(len(row)==6 and bool(row[4]) for row in links)
    result['link_lower_count'] = sum(len(row[5].split()) for row in links if len(row)==6)
    tools = dict(line.split('=', 1) for line in parts.get('tools', ()) if '=' in line)
    for tool in ('ip', 'bridge', 'brctl'):
        result['link_' + tool + '_available'] = (int(tools[tool]) if tools.get(tool) in ('0', '1') else None)
    return result
