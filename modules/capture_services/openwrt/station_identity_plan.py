"""Group saved stations by DHCP interface before assigning identities."""
from .relocation_identity import generate_relocation_hostname


def words(value):
    return value if isinstance(value,list) else str(value or "").split()


def build_station_batch(builder,targets):
    groups={}
    all_members={}
    for section,row in targets:
        networks=words(row.get("network"))
        for network in networks:
            all_members.setdefault(network,[]).append((section,row))
        if row.get("mode")!="sta":
            continue
        if len(networks)!=1:
            reason="\u672a\u5173\u8054\u552f\u4e00\u7684\u7f51\u7edc\u63a5\u53e3\uff08"+(", ".join(networks) or "\u7a7a")+"\uff09"
            builder.plan["skipped"].append(str(row.get("ssid",section))+" ["+section+"]\uff1a"+reason+"\u3002")
            continue
        groups.setdefault(networks[0],[]).append((section,row))
    for network,members in groups.items():
        net=builder.s["configs"].get("network",{}).get(network,{})
        peers=all_members[network]
        invalid=[section for section,row in peers if row.get("mode")!="sta" or row.get("rm_wds_backup") or words(row.get("network"))!=[network]]
        reason=None
        if network in {"lan","loopback"}:
            reason="\u4e0e\u5c40\u57df\u7f51\u900f\u660e\u6865\u63a5\uff0c\u4e0d\u4fee\u6539\u5c40\u57df\u7f51\u4e0a\u62a5\u540d\u79f0"
        elif invalid:
            reason="\u63a5\u53e3 "+network+" \u8fd8\u88ab\u975e\u72ec\u7acb\u4e0a\u7ea7\u7684\u914d\u7f6e "+", ".join(invalid)+" \u4f7f\u7528"
        elif net.get("proto")!="dhcp":
            reason="\u5173\u8054\u63a5\u53e3 "+network+" \u7684\u7c7b\u578b\u662f "+str(net.get("proto") or "\u672a\u914d\u7f6e")+"\uff0c\u4e0d\u662f\u81ea\u52a8\u53d6\u5730\u5740\u63a5\u53e3"
        elif net.get("device") or net.get("ifname"):
            reason="\u5173\u8054\u63a5\u53e3 "+network+" \u76f4\u63a5\u7ed1\u5b9a\u8bbe\u5907 "+str(net.get("device") or net.get("ifname"))+"\uff0c\u4e0d\u80fd\u6309\u72ec\u7acb\u65e0\u7ebf\u63a5\u53e3\u6279\u91cf\u6539\u5199"
        elif len(members)>256:
            reason="\u5171\u7528\u63a5\u53e3\u7684\u4e0a\u7ea7\u914d\u7f6e\u8d85\u8fc7\u6279\u91cf\u5904\u7406\u4e0a\u9650"
        if reason:
            for section,row in members:
                builder.plan["skipped"].append(str(row.get("ssid",section))+" ["+section+"]\uff1a"+reason+"\u3002")
            continue
        host=generate_relocation_hostname(net.get("hostname","")).replace("_","-")
        assignments=[]
        for section,row in members:
            address=builder.mac()
            assignments.append(dict(section=section,macaddr=address))
            old=str(row.get("ssid",section))+(" ["+section+"]" if len(members)>1 else "")
            builder.plan["identities"].append(dict(section=section,old=old,name=host,mac=address))
        if len(assignments)>1:
            builder.plan["operations"].append(dict(action="identity_group",config="network",section=network,hostname=host,members=assignments))
            builder.plan["steps"].append("\u63a5\u53e3 "+network+" \u7684 "+str(len(members))+" \u6761\u4e0a\u7ea7 Wi-Fi \u5171\u7528\u540d\u79f0 "+host+"\uff0c\u4ec5\u5199\u5165\u4e00\u6b21\uff1b\u5404\u6761 MAC \u72ec\u7acb\u66f4\u6362\uff0cWi-Fi \u540d\u79f0\u548c\u5bc6\u7801\u4fdd\u7559\u3002\u6700\u540e\u4e00\u6761\u6062\u590d\u65f6\u8fd8\u539f\u539f\u4e0a\u62a5\u540d\u79f0\u3002")
        else:
            member=assignments[0]
            builder.plan["operations"].append(dict(action="identity",config="wireless",section=member["section"],network=network,values=dict(identity_action="set",macaddr=member["macaddr"],hostname=host)))
    if not builder.plan["operations"]:
        raise ValueError("\u6ca1\u6709\u53ef\u6267\u884c\u7684\u4fee\u6539\uff1a\n"+"\n".join(builder.plan["skipped"]))
