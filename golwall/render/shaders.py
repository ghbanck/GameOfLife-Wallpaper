"""The two shader passes that turn cells into a wallpaper.

``ps_history`` runs over the world once per frame.  It keeps, for every cell,
how many generations it has been alive (for the birth-to-mature colour ramp)
and how much of its death trail is left (fading in wall-clock time), so the CPU
only ever uploads the cells themselves -- one bit each.

``ps_frame`` runs over every screen pixel: find the cell under it (wrapping
around the torus), colour it from the palette table, then lay the editor's
boxes and ghost pattern, the Blender-style grid, and the editing frame over it.
Zoomed out, a pixel stands for a Shrink x Shrink block of cells instead: it
takes the brightest of their colours, and every overlay asks whether *any* cell
of the block is hit, so a one-cell glider or cursor never falls between pixels.
"""

HLSL = r"""
struct VSOut { float4 pos : SV_Position; };

VSOut vs_fullscreen(uint id : SV_VertexID)
{
    VSOut o;
    float2 uv = float2((id << 1) & 2, id & 2);
    o.pos = float4(uv * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return o;
}

int wrapi(int v, int m)
{
    int r = v % m;
    return r < 0 ? r + m : r;
}

// ---------------------------------------------------------------- history ---
Texture2D<uint>   Cells   : register(t0);   // 32 cells per texel, cell x in bit x % 32
Texture2D<float2> History : register(t1);   // (age in generations, trail 0..1)

cbuffer HistoryParams : register(b0)
{
    float Inc;       // generations since the last pass (0 when only edited)
    float Decay;     // trail multiplier for the time since the last pass
    uint  Reset;     // 1: forget everything, the cells jumped
    float ResetAge;  // the age live cells start from after a reset
};

float2 ps_history(VSOut i) : SV_Target
{
    int2 c = int2(i.pos.xy);
    uint word = Cells.Load(int3(c.x >> 5, c.y, 0));
    bool alive = ((word >> (uint(c.x) & 31u)) & 1u) != 0u;
    if (Reset != 0u)
        return float2(alive ? ResetAge : 0.0, 0.0);
    float2 h = History.Load(int3(c, 0));
    float age = h.x;
    float trail = h.y * Decay;
    if (alive)
    {
        age = age > 0.5 ? min(age + Inc, 255.0) : 1.0;
    }
    else
    {
        if (age > 0.5)
            trail = 1.0;
        age = 0.0;
    }
    return float2(age, trail);
}

// ------------------------------------------------------------------ frame ---
Texture2D<float2> State : register(t0);
Texture2D<float4> Table : register(t1);    // 0..255 trail levels, 256..511 ages
Texture2D<uint>   Mask  : register(t2);    // the ghost pattern under the pointer

cbuffer FrameParams : register(b0)
{
    int2   Origin;        // this surface's top-left, in camera pixels
    int2   Offset;        // where the first cell's left/top edge falls (<= 0)
    int2   CellOrigin;    // world cell at the top-left of the camera
    int2   WorldSize;
    int    Zoom;
    uint   Flags;         // 1 grid, 2 smooth, 4 editing frame, 8 probe
    int2   SurfaceSize;
    float4 GridOpacity;   // per level
    int4   GridStep;      // per level; 0 = unused
    float4 FrameColour;   // rgb + strength
    float4 ProbeColour;
    int4   BoxRect0;      // x, y, w, h in world cells; w = 0: unused
    float4 BoxEdge0;
    float4 BoxFill0;
    int4   BoxRect1;
    float4 BoxEdge1;
    float4 BoxFill1;
    int4   MaskRect;
    float4 MaskColour;    // a = 0: no mask
    int    FrameWidth;
    int    Shrink;        // cells per pixel along each axis: 1, or 2..16 zoomed out
    int2   FPad;
};

float3 colour_of(int2 cell)
{
    float2 s = State.Load(int3(cell, 0));
    int index = s.x > 0.5 ? 256 + int(min(s.x, 255.0)) : int(saturate(s.y) * 255.0);
    return Table.Load(int3(index, 0, 0)).rgb;
}

// The brightest channel of any cell in the block wins, so a lone live cell
// still shows among the dead ones that share its pixel.
float3 block_colour(int2 cell)
{
    float3 c = colour_of(cell);
    int y = cell.y;
    [loop] for (int j = 0; j < Shrink; ++j)
    {
        int x = cell.x;
        [loop] for (int i = 0; i < Shrink; ++i)
        {
            c = max(c, colour_of(int2(x, y)));
            if (++x == WorldSize.x) x = 0;
        }
        if (++y == WorldSize.y) y = 0;
    }
    return c;
}

// The Shrink cells from offset d (wrapping at m) against a span [0, len):
// whether any falls inside it, and whether one is its first or last cell.
void span_hits(int d, int m, int len, out bool inside, out bool edge)
{
    inside = false;
    edge = false;
    [loop] for (int t = 0; t < Shrink; ++t)
    {
        int a = d + t;
        if (a >= m) a -= m;
        if (a < len)
        {
            inside = true;
            if (a == 0 || a == len - 1) edge = true;
        }
    }
}

float3 box(float3 c, int2 cell, int4 r, float4 edge, float4 fill)
{
    if (r.z <= 0 || r.w <= 0)
        return c;
    bool in_x, in_y, edge_x, edge_y;
    span_hits(wrapi(cell.x - r.x, WorldSize.x), WorldSize.x, r.z, in_x, edge_x);
    span_hits(wrapi(cell.y - r.y, WorldSize.y), WorldSize.y, r.w, in_y, edge_y);
    if (!in_x || !in_y)
        return c;
    if (fill.a > 0.0)
        c = lerp(c, fill.rgb, fill.a);
    if (edge_x || edge_y)
        c = lerp(c, edge.rgb, edge.a);
    return c;
}

// Whether any set cell of the ghost pattern falls in this pixel's block.
bool mask_hit(int2 cell)
{
    int mx0 = wrapi(cell.x - MaskRect.x, WorldSize.x);
    int my = wrapi(cell.y - MaskRect.y, WorldSize.y);
    [loop] for (int j = 0; j < Shrink; ++j)
    {
        if (my < MaskRect.w)
        {
            int mx = mx0;
            [loop] for (int i = 0; i < Shrink; ++i)
            {
                if (mx < MaskRect.z && Mask.Load(int3(mx, my, 0)) != 0u)
                    return true;
                if (++mx == WorldSize.x) mx = 0;
            }
        }
        if (++my == WorldSize.y) my = 0;
    }
    return false;
}

float4 ps_frame(VSOut i) : SV_Target
{
    int2 px = int2(i.pos.xy);
    if ((Flags & 8u) != 0u && (px.x & 63) < 8 && (px.y & 63) < 8)
        return float4(ProbeColour.rgb, 1.0);

    int2 local = px + Origin - Offset;          // never negative
    int2 steps = local / Zoom;
    int2 within = local - steps * Zoom;         // always 0 zoomed out, where Zoom is 1
    int2 cell = int2(wrapi(CellOrigin.x + steps.x * Shrink, WorldSize.x),
                     wrapi(CellOrigin.y + steps.y * Shrink, WorldSize.y));

    float3 c;
    if ((Flags & 2u) != 0u && Zoom > 1 && Shrink == 1)
    {
        float2 f = (float2(local) + 0.5) / float(Zoom) - 0.5;
        float2 fl = floor(f);
        float2 t = f - fl;
        int2 b = int2(fl);
        int x0 = wrapi(CellOrigin.x + b.x, WorldSize.x), x1 = wrapi(x0 + 1, WorldSize.x);
        int y0 = wrapi(CellOrigin.y + b.y, WorldSize.y), y1 = wrapi(y0 + 1, WorldSize.y);
        float3 top = lerp(colour_of(int2(x0, y0)), colour_of(int2(x1, y0)), t.x);
        float3 bottom = lerp(colour_of(int2(x0, y1)), colour_of(int2(x1, y1)), t.x);
        c = lerp(top, bottom, t.y);
    }
    else if (Shrink > 1)
    {
        c = block_colour(cell);
    }
    else
    {
        c = colour_of(cell);
    }

    c = box(c, cell, BoxRect0, BoxEdge0, BoxFill0);
    if (MaskColour.a > 0.0 && mask_hit(cell))
        c = lerp(c, MaskColour.rgb, MaskColour.a);
    c = box(c, cell, BoxRect1, BoxEdge1, BoxFill1);

    if ((Flags & 1u) != 0u)
    {
        [unroll] for (int k = 0; k < 4; ++k)
        {
            // Zoomed out, a line goes on each pixel whose block holds a
            // multiple of s, and levels under two pixels apart are skipped.
            // A block that runs over the world's seam holds cell 0, which
            // every level marks even where the world is no multiple of s.
            int s = GridStep[k];
            if (s > 0 && (Shrink == 1 || s >= 2 * Shrink))
            {
                int gx = wrapi(cell.x, s);
                int gy = wrapi(cell.y, s);
                if (within.x == 0 && (gx == 0 || gx > s - Shrink || cell.x > WorldSize.x - Shrink))
                    c = lerp(c, float3(1.0, 1.0, 1.0), GridOpacity[k]);
                if (within.y == 0 && (gy == 0 || gy > s - Shrink || cell.y > WorldSize.y - Shrink))
                    c = lerp(c, float3(1.0, 1.0, 1.0), GridOpacity[k]);
            }
        }
    }

    if ((Flags & 4u) != 0u)
    {
        int d = min(min(px.x, px.y), min(SurfaceSize.x - 1 - px.x, SurfaceSize.y - 1 - px.y));
        if (d < FrameWidth)
            c = lerp(c, FrameColour.rgb, FrameColour.a);
    }
    return float4(c, 1.0);
}
"""

FLAG_GRID, FLAG_SMOOTH, FLAG_FRAME, FLAG_PROBE = 1, 2, 4, 8
