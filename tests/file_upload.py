"""Test file upload functionality."""

import sys
from pathlib import Path
from googleapiclient.errors import HttpError
from src.mcp_server.tools import GoogleService
from src.config import settings

def check_folder_exists(folder_id: str):
    """Check if folder exists."""
    google_service = GoogleService()
    exists = google_service.drive_check_folder_exists(folder_id)
    print(f"Folder '{folder_id}': {'✅ exists' if exists else '❌ not found'}")
    return exists

def list_available_folders():
    """List available folders in the parent folder."""
    google_service = GoogleService()
    print(f"📁 Listing folders in parent folder '{settings.GOOGLE_DRIVE_PARENT_FOLDER_ID}'...")
    try:
        folders = google_service.drive_list_folders(settings.GOOGLE_DRIVE_PARENT_FOLDER_ID)
        if not folders:
            print("   No folders found.")
            return []

        print(f"   Found {len(folders)} folder(s):")
        for i, folder in enumerate(folders, 1):
            print(f"   {i}. {folder['name']} (ID: {folder['id']})")
        return folders
    except Exception as e:
        print(f"   ❌ Error listing folders: {e}")
        return []

def test_file_upload(folder_id: str = None):
    """Test file upload functionality.

    Args:
        folder_id: Optional folder ID. If not provided, will use default or prompt.
    """
    google_service = GoogleService()

    # Read the test image file
    file_path = Path("tests/test.jpg")
    if not file_path.exists():
        print(f"❌ Error: Test file not found at {file_path}")
        return False

    with open(file_path, "rb") as f:
        file_content = f.read()

    # Use provided folder_id or default
    if not folder_id:
        folder_id = "1SHtIi9z1me1pY"  # Default test folder ID

    filename = file_path.name

    print(f"📤 Uploading '{filename}' to folder '{folder_id}'...")

    try:
        # Call with correct parameters: filename, file_content, parent_id, mime_type
        folder_exists = check_folder_exists(folder_id)
        if not folder_exists:
            print(f"❌ Error: Folder '{folder_id}' not found (404)")
            return False
        result = google_service.drive_upload_file(
            filename=filename,
            file_content=file_content,
            parent_id=folder_id,
            mime_type="image/jpeg"
        )

        assert result is not None
        assert "id" in result
        print(f"✅ Upload successful!")
        print(f"   File ID: {result.get('id')}")
        print(f"   File name: {result.get('name', filename)}")
        return True

    except HttpError as e:
        if e.resp.status == 404:
            print(f"❌ Error: Folder '{folder_id}' not found (404)")
            print(f"   Please provide a valid folder ID as an argument:")
            print(f"   python tests/file_upload.py <folder_id>")
            print(f"\n   Or update the folder_id in the test file.")
        else:
            print(f"❌ Upload failed with HTTP error {e.resp.status}: {e}")
        return False

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return False


def test_video_upload():
    """Test video upload functionality."""
    google_service = GoogleService()

    # Example: if you have a test video file
    # file_path = Path("tests/test.mp4")
    # if not file_path.exists():
    #     print(f"Error: Test video file not found at {file_path}")
    #     return
    #
    # with open(file_path, "rb") as f:
    #     file_content = f.read()
    #
    # folder_id = "1SHtIi9z1me1pY"
    # filename = file_path.name
    #
    # result = google_service.drive_upload_file(
    #     filename=filename,
    #     file_content=file_content,
    #     parent_id=folder_id,
    #     mime_type="video/mp4"
    # )
    #
    # assert result is not None
    # assert "id" in result
    # print(f"✅ Video upload successful! File ID: {result.get('id')}")

    print("Video upload test skipped (no test video file)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "list":
            # List available folders
            list_available_folders()
        elif command == "check" and len(sys.argv) > 2:
            # Check if a specific folder exists
            folder_id = sys.argv[2]
            check_folder_exists(folder_id)
        else:
            # Use as folder_id for upload test
            folder_id = command
            print("Testing image upload...")
            success = test_file_upload(folder_id)

            if success:
                print("\nTesting video upload...")
                # test_video_upload()
            else:
                print("\n⚠️  Image upload failed. Skipping video upload test.")
                sys.exit(1)
    else:
        # No arguments - list folders and show usage
        print("📋 Usage:")
        print("   python tests/file_upload.py list              - List available folders")
        print("   python tests/file_upload.py check <folder_id> - Check if folder exists")
        print("   python tests/file_upload.py <folder_id>       - Upload test file to folder")
        print()
        print("Available folders:")
        folders = list_available_folders()
        if folders:
            print(f"\n💡 Tip: Use one of the folder IDs above to test upload:")
            print(f"   python tests/file_upload.py {folders[0]['id']}")