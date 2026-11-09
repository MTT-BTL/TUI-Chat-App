class AppError(Exception):
    """Lỗi nghiệp vụ để router chuyển thành DENY/ERROR, không phải lỗi socket."""

    def __init__(self, message: str, code: str = "BAD_ARGUMENT"):
        super().__init__(message)
        self.code = code
