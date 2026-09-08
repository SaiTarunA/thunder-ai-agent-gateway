import logging

logger = logging.getLogger(__name__)


class Utils:

    _instance = None

    def extract_user_name(self, user):
        try:
            name = user
            if "[V]" in name:
                name = name.split("[V]")[-1]
            if "_" in name:
                name = name.split("_")[-1]
            return name
        except Exception as e:
            logger.error(f"Error :: {e}")
            return user


utils = Utils()
