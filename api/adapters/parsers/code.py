from api.models.chunk import Chunk


class CodeParser:
    def supported_extensions(self) -> list[str]:
        return [".py", ".js", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java"]

    def parse(self, file_path: str) -> list[Chunk]:
        raise NotImplementedError("CodeParser implemented in Delivery 2")
