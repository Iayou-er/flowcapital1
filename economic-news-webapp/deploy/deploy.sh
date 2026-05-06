#!/bin/bash
# ========================================
# 一键部署脚本（在 ECS 上执行）
# ========================================
set -e

DOMAIN="your-domain.com"  # 替换为你的域名或 ECS 公网 IP
PROJECT_DIR="/opt/economic-news"

echo "=== 1. 安装系统依赖 ==="
apt update && apt install -y nginx redis-server python3 python3-pip python3-venv

echo "=== 2. 启动 Redis ==="
systemctl enable redis-server
systemctl start redis-server

echo "=== 3. 创建项目目录 ==="
mkdir -p $PROJECT_DIR
mkdir -p /var/www/economic-news

echo "=== 4. 上传项目文件（在本地执行 scp）==="
echo "请从本地机器执行以下命令上传文件："
echo "  scp -r economic-news-webapp/build/* root@$DOMAIN:/var/www/economic-news/"
echo "  scp -r backend/ root@$DOMAIN:$PROJECT_DIR/backend/"
echo "  scp -r graph_rag/ root@$DOMAIN:$PROJECT_DIR/graph_rag/"
echo "  scp -r scripts/ root@$DOMAIN:$PROJECT_DIR/scripts/"
echo "  scp deploy/.env.production root@$DOMAIN:$PROJECT_DIR/.env"
read -p "按回车键继续（确保文件已上传）..."

echo "=== 5. 安装 Python 依赖 ==="
cd $PROJECT_DIR
python3 -m venv venv
source venv/bin/activate
pip install -r scripts/requirements.txt

echo "=== 6. 安装后端服务 ==="
cp deploy/backend.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable backend.service
systemctl start backend.service

echo "=== 7. 配置 Nginx ==="
cp deploy/nginx.conf /etc/nginx/sites-available/economic-news
ln -sf /etc/nginx/sites-available/economic-news /etc/nginx/sites-enabled/economic-news
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo "=== 8. 开启 HTTPS（可选）==="
echo "如需 HTTPS，执行: certbot --nginx -d $DOMAIN"

echo "=== 部署完成 ==="
echo "前端: http://$DOMAIN"
echo "后端 API: http://$DOMAIN/api/health"
echo "后端服务状态: systemctl status backend.service"
echo "Nginx 状态: systemctl status nginx"
