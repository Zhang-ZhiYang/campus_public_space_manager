import qrcode
import qrcode.constants
from PIL import Image # Pillow 库通常是 qrcode 生成图片所必需的
from io import BytesIO
import os
import sys

def test_qrcode_generation(data_to_encode="Test booking ID: 12345"):
    print(f"--- Running standalone qrcode test ---")
    print(f"Python executable: {sys.executable}")
    print(f"qrcode module loaded from: {qrcode.__file__}")

    try:
        # 1. 检查 qrcode.QRCode 类的可用性
        if not hasattr(qrcode, 'QRCode'):
            raise AttributeError("qrcode module does not have a 'QRCode' class. Is the correct 'qrcode' library installed?")
        print("Successfully found qrcode.QRCode class.")

        # 2. 检查 qrcode.constants 的可用性
        if not hasattr(qrcode, 'constants'):
            raise AttributeError("qrcode module does not have a 'constants' attribute. Is the correct 'qrcode' library installed?")
        if not hasattr(qrcode.constants, 'ERROR_CORRECT_L'):
            raise AttributeError("qrcode.constants module does not have an 'ERROR_CORRECT_L' attribute.")
        print("Successfully found qrcode.constants.ERROR_CORRECT_L.")

        # 3. 实例化 QRCode 对象
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        print("qrcode.QRCode object instantiated successfully.")

        # 4. 添加数据
        if not hasattr(qr, 'add_data'):
            raise AttributeError("QRCode object does not have an 'add_data' method.")
        qr.add_data(data_to_encode)
        print(f"Added data: '{data_to_encode}'")

        # 5. 生成 QR 码
        if not hasattr(qr, 'make'):
            raise AttributeError("QRCode object does not have a 'make' method.")
        qr.make(fit=True)
        print("Successfully called qr.make(fit=True).")

        # 6. 生成图片
        if not hasattr(qr, 'make_image'):
            raise AttributeError("QRCode object does not have a 'make_image' method.")
        img = qr.make_image(fill_color="black", back_color="white")
        print("Successfully created QR code image.")

        # 7. 保存图片到文件
        output_filename = "standalone_qrcode_test.png"
        img.save(output_filename)
        print(f"QR code successfully saved to {os.path.abspath(output_filename)}")

        # 简单的验证
        if os.path.exists(output_filename) and os.path.getsize(output_filename) > 0:
            print("Test passed: QR code file exists and is not empty.")
        else:
            print("Test failed: QR code file was not created or is empty.")

    except ImportError as e:
        print(f"--- Test failed due to ImportError ---")
        print(f"Error: {e}")
        print("Hint: Ensure 'qrcode' and 'Pillow' libraries are installed. Run: pip install qrcode Pillow")
    except AttributeError as e:
        print(f"--- Test failed due to AttributeError ---")
        print(f"Error: {e}")
        print("Hint: This indicates a method or attribute is missing from the qrcode library. This could be due to a corrupted installation or a naming conflict.")
    except Exception as e:
        print(f"--- Test failed due to unexpected error ---")
        print(f"Error type: {type(e).__name__}, Message: {e}")
    print(f"--- Standalone qrcode test finished ---")

if __name__ == "__main__":
    test_qrcode_generation()