"""领域错误的公共形状：一个稳定的错误码，外加它与 HTTP 状态的对应。

**它为什么住在 domain。** 分层契约里 api 可以 import domain 与 application，而
application **不许** import api（``application-purity``）。「错误带码」这条口径要在这两边
同时成立，基类就只能落在两者都看得见的那一层——domain。这里只放一个字符串与一个 int，
没有任何 HTTP 库的 import，``domain-purity`` 仍然成立。

**它解决的是什么。** 同一个仓库里两种做法并存时，「状态码从哪来」就变成看运气：一种是
把码写在路由里（``raise HTTPException(404, ...)``，码跟着端点走），另一种是抛一个不带码的
领域异常、由每个路由各自记得该映射成几。后者在调用链变长之后必然漂开，而且**只有起了
HTTP 才验得到**。统一到「用例抛出带码的错误、api 只有一个处理器按码映射」之后，状态码
是**领域层的可测事实**：不起服务也能断言它。

**码是稳定契约，消息不是。** ``code`` 是调用方据以分支的东西（前端按它选文案），
``message`` 是给人看的诊断文本，随时可以更精确。所以码用固定字面量、消息用 f-string。
"""

from __future__ import annotations


class DomainError(Exception):
    """带错误码与 HTTP 状态的领域错误基类。

    子类只需要覆写 ``code`` / ``status_code`` 两个类属性；构造参数是**给人看的消息**，
    缺省时退回错误码本身——「没有消息」与「消息就是码」在响应里长得一样，不留空串。
    """

    code = "domain_error"
    status_code = 400

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
