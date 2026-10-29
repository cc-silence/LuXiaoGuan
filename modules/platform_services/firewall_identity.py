"""Public firewall labels and historical labels recognized only for migration."""
from modules.release_identity import PRODUCT_NAME

TEMPORARY_RULE_GROUP = f"{PRODUCT_NAME} Temporary Network Access"
LEGACY_TEMPORARY_RULE_GROUP = "RouterManager Temporary Network Access"
