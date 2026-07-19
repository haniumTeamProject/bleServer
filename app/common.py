from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


# 프론트가 camelCase JSON을 기대하는데(예: floorCount), 파이썬 컨벤션은 snake_case라서
# 필드는 snake_case로 쓰고 JSON으로 나갈 때만 camelCase로 자동 변환해주는 공용 베이스.
# Java에서는 필드 자체가 camelCase라 신경 안 써도 됐던 부분.
class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )
