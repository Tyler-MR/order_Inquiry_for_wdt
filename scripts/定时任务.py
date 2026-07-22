import datetime
import subprocess
import schedule
import time
import pytz
import warnings
import pandas as pd
import os
import sys

warnings.filterwarnings('ignore')
tz = pytz.timezone('Asia/Shanghai')

path = r'C:\Users\Financial\Desktop\PyCharm 2023.1.4\Financial_data'
order_project_path = r'C:\Users\Financial\wwwroot\order_Inquiry_for_wdt'
shop_owner_excel = r'C:\Company\Python导入数据\财务部\拼多多淘宝店铺信息表.xlsx'
product_spec_excel = r'C:\Users\Financial\Desktop\规格数量.xlsx'
product_master_excel = r'C:\Company\Python导入数据\表\产品成本佣金表.xlsx'
linux_ssh_key = r'C:\Users\Financial\.ssh\codex_linux_deploy_ed25519'


def send_file():
    script_path = os.path.join(path, r'send_file.py')
    process = subprocess.Popen(['python', script_path])
    process.wait()


schedule.every().day.at("08:00", tz).do(send_file)


def sync_shop_owner_map():
    """每天凌晨将店铺负责人映射同步到 Linux MySQL。"""
    sync_script = os.path.join(order_project_path, 'scripts', 'sync_shop_owner_map.py')
    python_exe = r'C:\Users\Financial\AppData\Local\Programs\Python\Python311\python.exe'
    if not os.path.isfile(python_exe):
        python_exe = sys.executable

    subprocess.run(
        [
            python_exe,
            sync_script,
            '--excel',
            shop_owner_excel,
            '--ssh-key',
            linux_ssh_key,
        ],
        cwd=order_project_path,
        check=False,
    )


schedule.every().day.at("01:00", tz).do(sync_shop_owner_map)


def sync_product_master():
    """每天将商品名称和商品规格同步到 Linux MySQL。"""
    sync_script = os.path.join(order_project_path, 'scripts', 'sync_product_master.py')
    python_exe = r'C:\Users\Financial\AppData\Local\Programs\Python\Python311\python.exe'
    if not os.path.isfile(python_exe):
        python_exe = sys.executable

    subprocess.run(
        [
            python_exe,
            sync_script,
            '--spec-excel',
            product_spec_excel,
            '--product-excel',
            product_master_excel,
            '--ssh-key',
            linux_ssh_key,
        ],
        cwd=order_project_path,
        check=False,
    )


schedule.every().day.at("01:00", tz).do(sync_product_master)


def mysql_sviptrader():
    script_path_7 = os.path.join(path, r'BI\MYSQL数据准备.py')
    subprocess.Popen(['python', script_path_7])


schedule.every().day.at("05:30", tz).do(mysql_sviptrader)


def financial():
    script_path_10 = os.path.join(path, r'财务部\利润率\0.日报利润率热度云数据.py')
    subprocess.Popen(['python', script_path_10])


schedule.every().day.at("05:00", tz).do(financial)


# def category_1():
#     script_path_12 = os.path.join(path, r'AI\ratting.py')
#     subprocess.Popen(['python', script_path_12])
#     script_path_13 = os.path.join(path, r'BI\一类电商_淘宝数据.py')
#     subprocess.Popen(['python', script_path_13])
#
#
# schedule.every().day.at("07:10", tz).do(category_1)


def after_sales():
    script_path_13 = os.path.join(path, r'运营部\拼多多\拼多多视频支付数据.py')
    subprocess.Popen(['python', script_path_13])

    script_path_19 = os.path.join(path, r'BI\淘宝视频信息.py')
    subprocess.Popen(['python', script_path_19])

    # script_path_14 = os.path.join(path, r'TEST\产品统计.py')
    # subprocess.Popen(['python', script_path_14])
    script_path_15 = os.path.join(path, r'BI\淘宝链接信息相关.py')
    subprocess.Popen(['python', script_path_15])
    script_path_16 = os.path.join(path, r'BI\拼多多淘宝利润率上传mysql.py')
    subprocess.Popen(['python', script_path_16])
    script_path_17 = os.path.join(path, r'运营部\拼多多\拼多多链接数据抓取.py')
    subprocess.Popen(['python', script_path_17])
    script_path_18 = os.path.join(path, r'运营部\拼多多\淘宝.py')
    subprocess.Popen(['python', script_path_18])

    # script_path_12 = os.path.join(path, r'财务部\日报\wdt_data_process.py')
    # subprocess.Popen(['python', script_path_12])
    if datetime.datetime.now(tz).weekday() == 5:  # 5表示周六
        script_path_15 = os.path.join(path, r'财务部\拼多多\拼多多视频支付数据.py')
        subprocess.Popen(['python', script_path_15])

    if datetime.datetime.now(tz).weekday() == 3:  # 3表示周四
        script_path_16 = os.path.join(path, r'客服部\拼多多客服接待.py')
        subprocess.Popen(['python', script_path_16])


schedule.every().day.at("07:50", tz).do(after_sales)


def day_file():
    script_path_14 = os.path.join(path, r'BI\BI_二类电商_厂家产品明细.py')
    subprocess.Popen(['python', script_path_14])
    script_path_15 = os.path.join(path, r'BI\二类电商_数据合并上传.py')
    subprocess.Popen(['python', script_path_15])


schedule.every().day.at("14:00", tz).do(day_file)


# def ratting():
#     script_path_13 = os.path.join(path, r'AI\ratting.py')
#     subprocess.Popen(['python', script_path_13])
#
#
# schedule.every().day.at("14:15", tz).do(ratting)


# def rate_send():
#     script_path_13 = os.path.join(path, r'TEST\视频号退货率.py')
#     subprocess.Popen(['python', script_path_13])
#
#
# schedule.every().day.at("17:10", tz).do(rate_send)


def del_file():
    script_path_14 = os.path.join(path, r'财务部\日报\new每日利润率.py')
    subprocess.Popen(['python', script_path_14])

    time.sleep(580)
    script_path_15 = os.path.join(path, r'发货部\昨日物流统计抽查.py')
    subprocess.Popen(['python', script_path_15])
    subprocess.Popen(['python', script_path_15])


schedule.every().day.at("08:15", tz).do(del_file)
while True:
    schedule.run_pending()
    time.sleep(60)
