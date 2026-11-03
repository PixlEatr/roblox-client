// Material density values from Roblox Creator Hub, retrieved 2026-11-02.
// https://create.roblox.com/docs/parts/materials#default-physical-properties
// Air and Water are terrain materials absent from the published table.
struct MaterialDensity { int value; double density; };
constexpr MaterialDensity kMaterialDensities[]={
    {1376,2.36}, // Asphalt
    {788,2.691}, // Basalt
    {848,1.922}, // Brick
    {2304,0.7}, // Cardboard
    {2305,1.1}, // Carpet
    {2306,2.4}, // CeramicTiles
    {2307,2}, // ClayRoofTiles
    {880,2.691}, // Cobblestone
    {816,2.403}, // Concrete
    {1040,7.85}, // CorrodedMetal
    {804,2.691}, // CrackedLava
    {1056,7.85}, // DiamondPlate
    {1312,0.7}, // Fabric
    {1072,2.7}, // Foil
    {1584,2.403}, // ForceField
    {1552,0.919}, // Glacier
    {1568,2.403}, // Glass
    {832,2.691}, // Granite
    {1280,0.9}, // Grass
    {1360,0.9}, // Ground
    {1536,0.919}, // Ice
    {1284,0.9}, // LeafyGrass
    {2309,0.86}, // Leather
    {820,2.691}, // Limestone
    {784,2.563}, // Marble
    {1088,7.85}, // Metal
    {1344,0.9}, // Mud
    {288,0.7}, // Neon
    {836,2.691}, // Pavement
    {864,2.403}, // Pebble
    {2310,0.75}, // Plaster
    {256,0.7}, // Plastic
    {896,2.691}, // Rock
    {2308,2.36}, // RoofShingles
    {2311,1.3}, // Rubber
    {1392,2.165}, // Salt
    {1296,1.602}, // Sand
    {912,2.691}, // Sandstone
    {272,0.7}, // SmoothPlastic
    {800,2.691}, // Slate
    {1328,0.9}, // Snow
    {512,0.35}, // Wood
    {528,0.35}, // WoodPlanks
};
