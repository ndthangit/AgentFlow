import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from filesystem_mcp.server import list_files, read_file, write_file


class FilesystemMcpTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name).resolve()
        patcher = patch("filesystem_mcp.server.FILE_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_write_read_and_list(self):
        self.assertEqual(
            write_file("notes/demo.txt", "xin chào"),
            {"path": "notes/demo.txt", "bytes": 9},
        )
        self.assertEqual(
            read_file("notes/demo.txt"),
            {"path": "notes/demo.txt", "content": "xin chào"},
        )
        self.assertEqual(
            list_files(),
            {"files": ["notes/demo.txt"], "truncated": False},
        )

    def test_paths_cannot_escape_data_root(self):
        for path in ("../secret.txt", str(self.root.parent / "secret.txt")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                write_file(path, "blocked")

    def test_overwrite_can_be_disabled(self):
        write_file("demo.txt", "first")
        with self.assertRaises(FileExistsError):
            write_file("demo.txt", "second", overwrite=False)


if __name__ == "__main__":
    unittest.main()
