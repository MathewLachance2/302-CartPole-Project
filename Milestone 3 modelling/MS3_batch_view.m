function fig = MS3_batch_view(runs,runFolder)
% Inspect the traces and fitted coefficients from one completed batch.
fig = uifigure('Name','MECH 302 | Batch simulation results','Tag','batchResults', ...
    'Position',[100 100 1080 565],'Visible','off');
page = uigridlayout(fig,[4 1]);
page.RowHeight = {32,24,'1x',30};
page.Padding = [16 12 16 12];
uilabel(page,'Text','Simulated damping across 63 runs','FontSize',20,'FontWeight','bold');
valid = isfinite(runs.fit_lambda_s_inv);
info = sprintf('1–3 bolts  ·  50–70 mm  ·  5 s free swing  ·  Assumed c = %.4g N·m·s/rad  ·  %d of 63 decay fits', ...
    runs.assumed_c_Nm_s_rad(1),sum(valid));
uilabel(page,'Text',info,'Tag','batchInfo');
tabs = uitabgroup(page);
plotTab = uitab(tabs,'Title','Coefficient plots');
distanceTab = uitab(tabs,'Title','Distance by configuration');
tableTab = uitab(tabs,'Title','Run table (63)');
plotGrid = uigridlayout(plotTab,[3 3]);
plotGrid.RowHeight = {32,'1x',36};
plotGrid.ColumnWidth = {'1x','1x','1x'};
options = uigridlayout(plotGrid,[1 2]);
options.Layout.Row = 1; options.Layout.Column = [1 3];
options.ColumnWidth = {70,160}; options.Padding = [0 0 0 0];
uilabel(options,'Text','Compare by');
group = uidropdown(options,'Items',{'Bolt configuration','Travel distance'}, ...
    'ValueChangedFcn',@plotFits,'Tag','groupRuns');
axesList = gobjects(3,1);
for k = 1:3
    axesList(k) = uiaxes(plotGrid,'Tag',sprintf('batchAxes%d',k));
    axesList(k).Layout.Row = 2; axesList(k).Layout.Column = k;
end
caption = uilabel(plotGrid,'Text','','FontSize',12,'WordWrap','on');
caption.Layout.Row = 3; caption.Layout.Column = [1 3];
distanceGrid = uigridlayout(distanceTab,[2 3]);
distanceGrid.RowHeight = {'1x',32};
boltNames = {'1 centre','2 outer','3 all'};
panelTitles = {'1 bolt (centre)','2 bolts (outer)','3 bolts (all)'};
% Share the b scale across panels. On each right axis the same height reads
% as c = Ip*b for that configuration. Keep zero in view for flat sweeps.
bLimits = fitLimits(runs.fit_b_s_inv);
for bolt = 1:3
    cases = sortrows(runs(runs.bolt_count==bolt,:),'distance_mm');
    ax = uiaxes(distanceGrid,'Tag',sprintf('distanceAxes%d',bolt));
    ax.Layout.Row = 1; ax.Layout.Column = bolt;
    yyaxis(ax,'left');
    curve = plot(ax,cases.distance_mm,cases.fit_b_s_inv,'-o', ...
        'Color',[.14 .47 .72],'LineWidth',1.4,'MarkerSize',4);
    curve.DataTipTemplate.DataTipRows = [dataTipTextRow('Distance (mm)',cases.distance_mm), ...
        dataTipTextRow('b (s⁻¹)',cases.fit_b_s_inv), ...
        dataTipTextRow('c (N·m·s/rad)',cases.fit_c_Nm_s_rad)];
    ylim(ax,bLimits); ylabel(ax,'b (s⁻¹)');
    if ~any(isfinite(cases.fit_b_s_inv))
        text(ax,.5,.5,'No resolved swing fits','Units','normalized','HorizontalAlignment','center');
    end
    yyaxis(ax,'right');
    ylim(ax,bLimits*cases.Ip_kg_m2(1)); ylabel(ax,'c (N·m·s/rad)');
    yyaxis(ax,'left');
    ax.YAxis(2).Color = ax.YAxis(1).Color;
    title(ax,panelTitles{bolt});
    xlim(ax,[49 71]); xticks(ax,50:5:70); xlabel(ax,'Travel distance (mm)'); grid(ax,'on');
end
distanceCaption = uilabel(distanceGrid,'Text', ...
    'Each marker is one simulated fit. Read b on the left and c on the right. The scales are related by c = Iₚ b.');
distanceCaption.Layout.Row = 2; distanceCaption.Layout.Column = [1 3];
tableGrid = uigridlayout(tableTab,[1 1]);
displayRuns = MS3_plotting_table(runs);
uit = uitable(tableGrid,'Data',displayRuns,'Tag','runTable','RowName',{}, ...
    'ColumnEditable',false,'ColumnSortable',true);
uit.ColumnName = displayRuns.Properties.VariableNames;
uit.ColumnWidth = 'auto';
footer = uigridlayout(page,[1 2]); footer.Padding = [0 0 0 0];
footer.ColumnWidth = {128,'1x'};
uibutton(footer,'Text','Open CSV folder','ButtonPushedFcn',@(~,~)winopen(char(runFolder)));
[~,batchName] = fileparts(runFolder);
uilabel(footer,'Text',char(fullfile('Simulated_runs',batchName)),'Tooltip',char(runFolder));
plotFits();
movegui(fig,'center'); fig.Visible = 'on';

    function plotFits(~,~)
        byBolt = strcmp(group.Value,'Bolt configuration');
        fields = {'fit_lambda_s_inv','fit_b_s_inv','fit_c_Nm_s_rad'};
        titles = {'Decay rate','Normalized damping','Rotational damping'};
        units = {'λ (s⁻¹)','b (s⁻¹)','c (N·m·s/rad)'};
        colours = [.15 .55 .85; .94 .52 .14; .37 .70 .39];
        markers = {'o','s','d'};
        for j = 1:3
            ax = axesList(j);
            legend(ax,'off'); cla(ax); hold(ax,'on'); grid(ax,'on');
            values = runs.(fields{j});
            for b = 1:3
                rows = runs.bolt_count==b & isfinite(values);
                distances = runs.distance_mm(rows);
                if byBolt
                    x = b + (distances-60)*.026;
                else
                    x = distances;
                end
                pts = scatter(ax,x,values(rows),36, ...
                    'Marker',markers{b},'MarkerEdgeColor',colours(b,:), ...
                    'MarkerFaceColor',colours(b,:),'DisplayName',boltNames{b});
                if any(rows)
                    pts.DataTipTemplate.DataTipRows = [dataTipTextRow('Bolts',runs.bolt_count(rows)), ...
                        dataTipTextRow('Distance (mm)',distances),dataTipTextRow(units{j},values(rows))];
                end
            end
            title(ax,titles{j}); ylabel(ax,units{j});
            if byBolt
                xlim(ax,[.55 3.45]); xticks(ax,1:3); xticklabels(ax,boltNames);
                xlabel(ax,'Installed bolts');
            else
                xlim(ax,[49 71]); xticks(ax,50:5:70); xticklabels(ax,'auto'); xlabel(ax,'Travel distance (mm)');
                if j==1, legend(ax,'Location','best','FontSize',9); end
            end
            usable = values(isfinite(values));
            if isempty(usable)
                text(ax,.5,.5,'No resolved swing fits','Units','normalized','HorizontalAlignment','center');
            else
                ylim(ax,fitLimits(usable));
            end
        end
        if byBolt
            caption.Text = 'Each marker is one simulated run. Within each bolt group, distance increases from 50 mm on the left to 70 mm on the right.';
        else
            caption.Text = 'Each marker is one simulated run. Fits can overlap because all runs use the same assumed rotational damping.';
        end
    end
end

function limits = fitLimits(values)
values = [0;values(isfinite(values))];
lo = min(values); hi = max(values);
pad = .08*(hi-lo);
if pad == 0, pad = 1e-6; end
limits = [lo-pad,hi+pad];
end
