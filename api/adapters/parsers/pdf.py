from api.models.chunk import Chunk


class PDFParser:
    def supported_extensions(self) -> list[str]:
        return [".pdf"]

    def parse(self, file_path: str) -> list[Chunk]:
        raise NotImplementedError("PDFParser implemented in Delivery 2")
