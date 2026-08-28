正向（用 default/example 值验证成功响应）
缺参（逐个移除必填参数 → 4xx）
类型错误（string↔int 互换 → 422/400）
枚举合法/非法（枚举每项 / 枚举外值 → 4xx）
越界（minimum-1 / maximum+1、长度超限、format 违规）
鉴权异常（无 token 401 / 错误 token 403）
资源不存在（不存在的资源 ID → 404）