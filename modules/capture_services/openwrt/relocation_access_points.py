"""Password-free access-point identity adapter shared with initial setup."""
from .ap_batch_identity import build_access_point_identities


def access_point_snapshot(review):
    wireless = {row['section']:dict(row, **{'.type':'wifi-iface'})
                for row in review['access_points']}
    addresses = list(review['addresses']) + list(review.get('plan', {}).get('macs', ()))
    return dict(configs=dict(wireless=wireless, network={}),
                devices={str(i):dict(macaddr=mac) for i,mac in enumerate(addresses)})


def relocation_identities(review, requested=None):
    if not review['access_points']:
        if requested not in (None, []):
            raise ValueError('Access-point list changed; reopen the preview.')
        return []
    return build_access_point_identities(access_point_snapshot(review), requested)
