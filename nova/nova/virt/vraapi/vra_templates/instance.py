class InstanceTemplate(object):

    @staticmethod
    def instance_template():
        return {
            "description": None,
            "tags": [],
            "flavor": None,
            "disks": [],
            "customProperties": {},
            "bootConfig": {
                "content": ""
            },
            "bootConfigSettings": {
                "phoneHomeShouldWait": True,
                "phoneHomeFailOnTimeout": False,
                "phoneHomeTimeoutSeconds": 100
            },
            "name": None,
            "imageRef": None,
            "projectId": None,
            "storage": {
                "constraints": {
                    "tag": []
                }
            },
            "nics": []
        }
