
# ==========         type of DataBases         =================

DB_STREAMS = 'STREAMS'
DB_LIVEPBX = 'LIVEPBX'
DB_WAREHOUSEPBX = 'WAREHOUSE'
DB_CLOUD = 'CLOUD'
DB_PARTNERS = 'PARTNERS'
DB_CONTACTS = 'CONTACTS'
DB_OPENSIPS = 'OPENSIPS'
DB_WSAUTH = 'WSAUTH'

# ---------------------


# ==========         type of connections       ===================

DB_SELECT_OPERQATION = 'slave'
DB_MODIFY_OPERQATION = 'master'

# ==========          operation limits         ====================

DB_FAILOVER_RETRY_COUNT = 3
DB_QUERY_TIMEOUT_LIMIT = 10000


# ==========             DB Details            ======================

DB_CONFIGS = {

    "STREAMS": {
        "master": ['192.168.1.5', '192.168.1.5'],
        "slave": ['192.168.1.5', '192.168.1.5'],
        "user": 'devusr',
        "password": 'nBLRq8kyf1iv1K99',
        "database": 'streams',
        "connectionLimit": 10,
        "connectTimeout": 30, # Connection timeout in ms
    },
    "WAREHOUSE": {
        "master": ['192.168.1.54', '192.168.1.54'],
        "slave": ['192.168.1.54', '192.168.1.54'],
        "user": 'devusr',
        "password": 'nBLRq8kyf1iv1K99',
        "database": 'warehousepbx',
        "connectionLimit": 10,
        "connectTimeout": 30, # Connection timeout in ms
    },
    "OPENSIPS": {
        "master": ['192.168.1.51', '192.168.1.51'],
        "slave": ['192.168.1.51', '192.168.1.51'],
        "user": 'devusr',
        "password": 'nBLRq8kyf1iv1K99',
        "database": 'opensips',
        "connectionLimit": 10,
        "connectTimeout": 30, # Connection timeout in ms
    },
    "CLOUD": {
        "master": ['192.168.1.51', '192.168.1.51'],
        "slave": ['192.168.1.51', '192.168.1.51'],
        "user": 'devusr',
        "password": 'nBLRq8kyf1iv1K99',
        "database": 'wsclouddrive',
        "connectionLimit": 10,
        "connectTimeout": 30, # Connection timeout in ms
    },
    "LIVEPBX": {
        "master": ["192.168.1.51", "192.168.1.51"],
        "slave": ["192.168.1.51", "192.168.1.51"],
        "user": "devusr",
        "password": "nBLRq8kyf1iv1K99",
        "database": "livepbx",
        "connectionLimit": 10,
        "connectTimeout": 30,  # Connection timeout in ms
    },
}
