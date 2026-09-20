import plotly.graph_objects as go
import numpy as np

def plot_clarke_error_grid(ref_bgl=None, pred_bgl=None, benchmark_points=None):
    """
    Renders an interactive Clarke Error Grid Analysis (EGA) chart with standard clinical zones.
    """
    fig = go.Figure()

    # 1. Background boundary polygon lines
    # Diagonal ideal line
    fig.add_trace(go.Scatter(
        x=[0, 400], y=[0, 400], mode='lines',
        line=dict(color='#64748b', width=1.5, dash='dash'),
        name='Ideal (y = x)',
        hoverinfo='skip'
    ))

    # Zone A boundaries
    fig.add_trace(go.Scatter(
        x=[0, 70, 400], y=[20, 70, 320], mode='lines',
        line=dict(color='#16a34a', width=1.5),
        name='Zone A/B Lower Margin (y = 0.8x)',
        hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=[0, 70, 333.3], y=[70, 84, 400], mode='lines',
        line=dict(color='#16a34a', width=1.5),
        name='Zone A/B Upper Margin (y = 1.2x)',
        hoverinfo='skip'
    ))

    # Zone C boundaries
    fig.add_trace(go.Scatter(
        x=[70, 290], y=[180, 400], mode='lines',
        line=dict(color='#ea580c', width=1.5),
        name='Zone C Upper (y = x + 110)',
        hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=[130, 180, 180], y=[0, 70, 70], mode='lines',
        line=dict(color='#ea580c', width=1.5),
        name='Zone C Lower (y = 7/5x - 182)',
        hoverinfo='skip'
    ))

    # Zone D/E horizontal and vertical threshold lines
    fig.add_trace(go.Scatter(
        x=[70, 70], y=[100, 400], mode='lines',
        line=dict(color='#dc2626', width=1.5, dash='dot'),
        name='Zone D Hypo Miss Threshold (x=70)',
        hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=[180, 400], y=[70, 70], mode='lines',
        line=dict(color='#dc2626', width=1.5, dash='dot'),
        name='Zone D Hyper Miss Threshold (y=70)',
        hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=[180, 400], y=[100, 100], mode='lines',
        line=dict(color='#dc2626', width=1.5, dash='dot'),
        name='Zone D Hyper Miss Upper (y=100)',
        hoverinfo='skip'
    ))

    # Zone Annotations
    fig.add_annotation(x=300, y=280, text="<b>Zone A</b><br>(Clinically Accurate)", showarrow=False, font=dict(color="#15803d", size=11))
    fig.add_annotation(x=320, y=180, text="<b>Zone B</b><br>(Benign Error)", showarrow=False, font=dict(color="#854d0e", size=10))
    fig.add_annotation(x=170, y=340, text="<b>Zone B</b>", showarrow=False, font=dict(color="#854d0e", size=10))
    fig.add_annotation(x=120, y=270, text="<b>Zone C</b><br>(Over-correction)", showarrow=False, font=dict(color="#c2410c", size=9))
    fig.add_annotation(x=40, y=140, text="<b>Zone D</b><br>(Hypo Miss)", showarrow=False, font=dict(color="#b91c1c", size=9))
    fig.add_annotation(x=300, y=50, text="<b>Zone D</b><br>(Hyper Miss)", showarrow=False, font=dict(color="#b91c1c", size=9))
    fig.add_annotation(x=40, y=300, text="<b>Zone E</b><br>(Opposite)", showarrow=False, font=dict(color="#7f1d1d", size=9))
    fig.add_annotation(x=300, y=20, text="<b>Zone E</b><br>(Opposite)", showarrow=False, font=dict(color="#7f1d1d", size=9))

    # Plot benchmark presets points if provided
    if benchmark_points:
        bx = [p["ref"] for p in benchmark_points]
        by = [p["pred"] for p in benchmark_points]
        bnames = [p["name"] for p in benchmark_points]
        fig.add_trace(go.Scatter(
            x=bx, y=by, mode='markers+text',
            marker=dict(size=12, color='#0284c7', line=dict(color='white', width=1.5)),
            text=[f"{n.split(':')[0]}" for n in bnames], textposition="bottom right",
            name='Benchmark Scenarios',
            hovertext=[f"<b>{n}</b><br>Reference BGL: {r} mg/dL<br>Predicted BGL: {p} mg/dL<br>Error: {abs(p-r):.1f} mg/dL" for n, r, p in zip(bnames, bx, by)],
            hoverinfo='text'
        ))

    # Plot current test point
    if ref_bgl is not None and pred_bgl is not None and ref_bgl > 0:
        fig.add_trace(go.Scatter(
            x=[ref_bgl], y=[pred_bgl], mode='markers',
            marker=dict(size=18, color='#dc2626', symbol='diamond', line=dict(color='white', width=2)),
            name='Current Reading',
            hovertext=f"<b>Current Test</b><br>Reference: {ref_bgl} mg/dL<br>Predicted: {pred_bgl} mg/dL<br>Abs Error: {abs(pred_bgl - ref_bgl):.1f} mg/dL",
            hoverinfo='text'
        ))

    fig.update_layout(
        title="<b>Clarke Error Grid Analysis (EGA) — Clinical Safety Assessment</b>",
        xaxis=dict(title="Reference / Fingerstick Blood Glucose (mg/dL)", range=[0, 400], dtick=50, showgrid=True),
        yaxis=dict(title="Predicted Blood Glucose (mg/dL)", range=[0, 400], dtick=50, showgrid=True),
        height=500, width=600,
        margin=dict(l=20, r=20, t=40, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
        plot_bgcolor="#f8fafc"
    )
    return fig

if __name__ == "__main__":
    b_pts = [
        {"name": "Healthy Adult", "ref": 88.0, "pred": 91.3},
        {"name": "Prediabetes", "ref": 114.0, "pred": 135.0},
        {"name": "Type 2 Post-Meal", "ref": 172.0, "pred": 181.5},
        {"name": "Severe Hyperglycemia", "ref": 265.0, "pred": 257.5},
        {"name": "Hypoglycemia Alert", "ref": 62.0, "pred": 95.3}
    ]
    f = plot_clarke_error_grid(ref_bgl=88.0, pred_bgl=91.3, benchmark_points=b_pts)
    print("Clarke Error Grid figure created successfully!")
