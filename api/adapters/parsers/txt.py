from api.models.chunk import Chunk


class TXTParser:
    def supported_extensions(self) -> list[str]:
        return [".txt"]

    def parse(self, file_path: str) -> list[Chunk]:
        raise NotImplementedError("TXTParser implemented in Delivery 2")
