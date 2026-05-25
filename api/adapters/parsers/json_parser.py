from api.models.chunk import Chunk


class JSONParser:
    def supported_extensions(self) -> list[str]:
        return [".json"]

    def parse(self, file_path: str) -> list[Chunk]:
        raise NotImplementedError("JSONParser implemented in Delivery 2")
