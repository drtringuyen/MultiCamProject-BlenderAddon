// Written by the MultiCamProject Blender add-on (Export). Put the export folder inside
// Assets/: this script lands in Editor/ next to it.
// First import only - once a texture or model has import settings, the dev's changes stay.
using UnityEditor;

public class MCPTexturePostprocessor : AssetPostprocessor
{
    void OnPreprocessTexture()
    {
        if (!assetImporter.importSettingsMissing) return;
        var ti = (TextureImporter)assetImporter;
        var n = System.IO.Path.GetFileName(assetPath);
        if (n.StartsWith("ALB_"))
        {
            ti.textureType = TextureImporterType.Default;
            ti.sRGBTexture = true;
            ti.maxTextureSize = 8192;
        }
        if (n.StartsWith("NOR_"))
        {
            ti.textureType = TextureImporterType.NormalMap;   // OpenGL (Y+), same as Blender
            ti.sRGBTexture = false;
            ti.maxTextureSize = 8192;
        }
    }

    void OnPreprocessModel()
    {
        if (!assetImporter.importSettingsMissing) return;
        ((ModelImporter)assetImporter).materialImportMode =
            ModelImporterMaterialImportMode.ImportViaMaterialDescription;
    }
}
