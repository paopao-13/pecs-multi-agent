"""
文件读取工具安全性单元测试

测试 tools/file_reader.py：
- 空路径 / 不存在文件返回错误
- 隐藏文件 / 路径遍历 / 系统敏感路径被拒绝
- 项目内正常文件可读取

接口说明（已通过阅读源码确认）：
    from tools.file_reader import file_reader
    result = file_reader({"path": "some_file"})
    # result 为字符串：
    #   - 成功: 文件内容
    #   - 失败: "错误：..." 开头的提示
"""
import os
from tools.file_reader import file_reader


def test_read_empty_path():
    """空路径返回错误"""
    result = file_reader({"path": ""})
    assert "错误" in result
    assert "path" in result or "缺少" in result


def test_read_nonexistent():
    """不存在的文件返回错误"""
    result = file_reader({"path": "this_file_does_not_exist_xyz_12345.txt"})
    assert "错误" in result
    assert "不存在" in result


def test_read_hidden_file():
    """.env 隐藏文件被拒绝"""
    result = file_reader({"path": ".env"})
    assert "错误" in result
    assert "隐藏" in result


def test_read_path_traversal():
    """路径遍历 ../../../etc/passwd 被拒绝"""
    result = file_reader({"path": "../../../etc/passwd"})
    assert "错误" in result
    assert "禁止" in result


def test_read_system_path_windows():
    """Windows 系统敏感路径 C:\\Windows\\System32\\config\\SAM 被拒绝"""
    result = file_reader({"path": r"C:\Windows\System32\config\SAM"})
    assert "错误" in result
    assert "禁止" in result or "敏感" in result


def test_read_normal_file():
    """读取项目内的正常文件（config.py）成功"""
    # 使用绝对路径定位 config.py，避免相对路径引入 ".." 被误判为隐藏文件
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config.py")

    result = file_reader({"path": config_path})
    # 不应返回错误
    assert "错误" not in result
    # config.py 源码中包含的关键标识符
    assert "DEEPSEEK_API_KEY" in result
