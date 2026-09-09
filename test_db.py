import pymysql
import ssl
import os

# 证书路径（根据你的实际位置调整）
ca_cert_path = "isrgrootx1.pem"

# 如果证书文件不存在，尝试使用系统证书
if not os.path.exists(ca_cert_path):
    ca_cert_path = "/etc/ssl/cert.pem"  # macOS 或 Linux

ssl_context = ssl.create_default_context(cafile=ca_cert_path)
# 如果主机名验证失败，可以尝试关闭（不推荐生产环境）
ssl_context.check_hostname = True

try:
    conn = pymysql.connect(
        host="gateway01.ap-southeast-1.prod.alicloud.tidbcloud.com",
        port=4000,
        user="MNxaUkPFDa89Cin.root",
        password="HpCkJ0i058AKlwp7",  # 请替换
        database="qqbind_db",
        ssl=ssl_context,
        connect_timeout=10,
        charset="utf8mb4"
    )
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    result = cursor.fetchone()
    print("✅ 连接成功！结果:", result)
    conn.close()
except Exception as e:
    print("❌ 连接失败:", e)