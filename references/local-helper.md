# 本地Agent辅助命令

路径相对根目录。安装后优先使用assets/workbench/.venv/Scripts/python.exe运行脚本（mac/Linux版未实机验收）。脚本没有网络调用，不接受抖音Cookie或供应商密钥。

```
python scripts/workbench.py plan-profile --input profile.json
python scripts/workbench.py apply-profile --input profile.json --expected-revision <plan返回的expected_revision>
python scripts/workbench.py status
```

这两个profile命令仅操作本包runtime/的profile/keywords/categories/creator_domain/traffic_tags；不自动开启排程、不设置单价、不导入作者、不改密码。遇revision过期重新预览，不覆盖其他端修改。

关注读取结果保存为following.json，格式如下；时间、昵称与URL必须来自实际可见页面。evidence是可见依据而非Agent猜测；只看到昵称和链接就写“本人关注列表可见”，不能假设领域。

```json
{
  "captured_at": "2026-11-07T12:00:00+08:00",
  "page_url": "https://www.douyin.com/user/self",
  "status": "partial",
  "items": [{"name": "示例昵称", "url": "https://www.douyin.com/user/EXAMPLE_SEC_USER_ID", "evidence": "本人关注列表可见；这里只是结构示例，不能真实导入"}]
}
```

```
python scripts/workbench.py save-following --input following.json
python scripts/workbench.py select-following --ids <返回的真实ID1> <真实ID2>
```

save-following只保存本地候选，**不**监控全部关注；select-following仅启用明确选择的作者，最多100位，不自动删除原对标。候选每批最多500位，未见明确到底状态标partial。空列表异常不记录为读取成功。候选文件可能含私人关注偏好，只保存在runtime内，不进入分享ZIP。

此CLI是本地文件工作流，限有本机读写权的Agent；它不绕过工作台网页登录，不生成或读取会话Cookie。没有命令工具的宿主改用网页设置和逐个添加对标；没有浏览器工具则不能自动读取账号。
