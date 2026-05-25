from api.models.chunk import Chunk


class CSVParser:
    def supported_extensions(self) -> list[str]:
        return [".csv"]

    def parse(self, file_path: str) -> list[Chunk]:
        raise NotImplementedError("CSVParser implemented in Delivery 2")
