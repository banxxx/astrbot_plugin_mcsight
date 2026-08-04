from skinpy import Skin, Perspective
from PIL import Image

# 1. 加载皮肤
skin = Skin.from_path("steve.png")        # 假设 steve.png 在当前目录

# 2. 设定视角
perspective = Perspective(
    x="left",          # 身体朝向
    y="front",         # 视角正面
    z="up",            # 俯视
    scaling_factor=6   # 图片大小倍数，6 即 6×原始像素尺寸
)

# 3. 渲染 3D 图
image_3d = skin.to_isometric_image(perspective)

# 4. 保存结果
image_3d.save("steve_3d.png")
print("渲染完成，已保存 steve_3d.png，尺寸：", image_3d.size)