from api.models.chunk import Chunk


class MarkdownParser:
    def supported_extensions(self) -> list[str]:
        return [".md", ".markdown"]

    def parse(self, file_path: str) -> list[Chunk]:
        raise NotImplementedError("MarkdownParser implemented in Delivery 2")
